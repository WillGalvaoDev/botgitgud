import os
import re
import statistics
import asyncio
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from dotenv import load_dotenv
import discord
from discord.ext import commands

from botgitgud.analysis.cadence import compute_cadence, is_eligible
from botgitgud.analysis.comparison import compare_spell_usage
from botgitgud.blizzard.client import BlizzardClient, BlizzardClientConfig
from botgitgud.domain.blacklist import MAJOR_CD_BLACKLIST
from botgitgud.domain.spells import SpellCatalog
from botgitgud.errors import ApiError
from botgitgud.report.text import ReportHeader, chunk_report_for_discord, render_report
from botgitgud.wcl.client import WclClient, WclClientConfig

load_dotenv()

WCL_CLIENT_ID = os.getenv("WCL_CLIENT_ID")
WCL_CLIENT_SECRET = os.getenv("WCL_CLIENT_SECRET")
BLIZZARD_CLIENT_ID = os.getenv("BLIZZARD_CLIENT_ID")
BLIZZARD_CLIENT_SECRET = os.getenv("BLIZZARD_CLIENT_SECRET")
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")

# T0.3: raw requests.post to the WCL API replaced by WclClient (timeouts,
# retry/backoff, auth-token caching, rate-limit floor). See docs/desvios.md D-7.
_wcl_client = WclClient(
    WclClientConfig(client_id=WCL_CLIENT_ID or "", client_secret=WCL_CLIENT_SECRET or "")
)

# T0.7: legacy's own OAuth/HTTP calls to Blizzard replaced by BlizzardClient
# (T0.4/D-8), and the LOCAL_SPELL_DB globals + get_spell_data/
# save_spell_to_local_db replaced by SpellCatalog (T0.4) — no more `category`
# persisted, no more concurrent open(path, "w") from worker threads.
_blizzard_client = BlizzardClient(
    BlizzardClientConfig(
        client_id=BLIZZARD_CLIENT_ID or "", client_secret=BLIZZARD_CLIENT_SECRET or ""
    )
)
_spell_catalog = SpellCatalog(Path("spells.json"), blizzard=_blizzard_client)

DEBUG = True


################################################################################
# UTILS
################################################################################
def parse_report_input(input_str):
    fight_match = re.search(r"fight=(\d+)", input_str)
    fight_id = int(fight_match.group(1)) if fight_match else None

    code_match = re.search(r"reports/([a-zA-Z0-9]{16})", input_str)
    if code_match:
        report_code = code_match.group(1)
    else:
        clean_code = input_str.split("?")[0].split("#")[0].strip()
        report_code = clean_code if len(clean_code) == 16 else input_str

    return report_code, fight_id


################################################################################
# MÓDULO: CAPTURA DE TIMELINE
################################################################################
def fetch_player_timeline_data(report_code, fight_id, char_name):
    query_meta = """
    query GetPlayerMeta($code: String!, $fightIDs: [Int]!) {
      reportData {
        report(code: $code) {
          fights(fightIDs: $fightIDs) {
            id encounterID name startTime endTime kill difficulty
          }
          table(fightIDs: $fightIDs, dataType: Summary, translate: true)
          castsTable: table(fightIDs: $fightIDs, dataType: Casts, translate: true)
        }
      }
    }
    """
    try:
        res_json = _wcl_client.query(
            query_meta, {"code": report_code, "fightIDs": [fight_id]}, op_name="fetch_player_meta"
        )
        data = res_json.get("data", {}).get("reportData", {}).get("report", {})
        fights = data.get("fights", [])
        if not fights:
            return None

        selected_fight = fights[0]
        start_time_ms = selected_fight["startTime"]
        end_time_ms = selected_fight["endTime"]
        duration_sec = (end_time_ms - start_time_ms) / 1000.0

        summary_data = data.get("table", {}).get("data", {})
        player_details = summary_data.get("playerDetails", {})

        user_spec, user_class, player_id = "Unknown", "Unknown", None
        user_server, user_region = None, None
        for role_group in ["dps", "tanks", "healers"]:
            for p in player_details.get(role_group, []):
                if p.get("name", "").lower() == char_name.lower():
                    player_id = p.get("id")
                    user_class = p.get("type", "Unknown")
                    user_server = p.get("server")
                    user_region = p.get("region")
                    specs = p.get("specs", [])
                    user_spec = (
                        specs[0].get("spec", "Unknown")
                        if specs and isinstance(specs[0], dict)
                        else (specs[0] if specs else "Unknown")
                    )
                    break

        if not player_id:
            return None

        # T0.7 (achado 3.11): DPS do próprio jogador, extraído do mesmo
        # table(dataType: Summary) já buscado — sem query extra.
        damage_total = None
        for entry in summary_data.get("damageDone", []):
            if entry.get("id") == player_id:
                damage_total = entry.get("total")
                break

        casts_entries = data.get("castsTable", {}).get("data", {}).get("entries", [])
        for entry in casts_entries:
            if entry.get("id") == player_id:
                for ab in entry.get("abilities", []):
                    guid = ab.get("guid") or ab.get("id")
                    name = ab.get("name")
                    if guid and name:
                        s_id = int(guid)
                        _spell_catalog.learn(s_id, name, "wcl")

        timeline_by_id = defaultdict(list)
        current_start = start_time_ms

        query_events = """
        query GetPlayerEvents($code: String!, $fightIDs: [Int]!, $startTime: Float!, $endTime: Float!) {
          reportData {
            report(code: $code) {
              events(fightIDs: $fightIDs, dataType: Casts, startTime: $startTime, endTime: $endTime, limit: 5000, translate: true) {
                data
                nextPageTimestamp
              }
            }
          }
        }
        """

        while current_start < end_time_ms:
            try:
                ev_res_json = _wcl_client.query(
                    query_events,
                    {
                        "code": report_code,
                        "fightIDs": [fight_id],
                        "startTime": current_start,
                        "endTime": end_time_ms,
                    },
                    op_name="fetch_player_events",
                )
            except ApiError:
                break

            ev_json = (
                ev_res_json.get("data", {})
                .get("reportData", {})
                .get("report", {})
                .get("events", {})
            )
            events_data = ev_json.get("data", [])

            for ev in events_data:
                if ev.get("sourceID") == player_id and ev.get("type") == "cast":
                    spell_id = ev.get("abilityGameID") or ev.get("ability")
                    if not spell_id:
                        continue

                    s_id = int(spell_id)
                    rel_sec = round(
                        (ev.get("timestamp", start_time_ms) - start_time_ms) / 1000.0, 1
                    )
                    timeline_by_id[s_id].append(rel_sec)

            next_page = ev_json.get("nextPageTimestamp")
            if not next_page or next_page <= current_start or next_page >= end_time_ms:
                break
            current_start = next_page

        dps = (damage_total / duration_sec) if (damage_total and duration_sec > 0) else None

        return {
            "fight": {
                "fight_id": fight_id,
                "encounter_id": selected_fight["encounterID"],
                "boss_name": selected_fight["name"],
                "duration_sec": duration_sec,
                "difficulty": selected_fight.get("difficulty"),
            },
            "build": {
                "class": user_class,
                "spec": user_spec,
                "server": user_server,
                "region": user_region,
            },
            "timeline": timeline_by_id,
            "dps": dps,
        }
    except Exception as e:
        print(f"❌ [ERRO FETCH TIMELINE]: {e}")
        return None


################################################################################
# PERFIL DE REFERÊNCIA DE MAJOR CDS (Filtro ajustado para 100+ logs e duração do player + 30s)
################################################################################
def fetch_top_logs_for_cds(encounter_id, user_class, user_spec, target_duration_sec):
    if DEBUG:
        print(
            f"🔎 [DEBUG RANKINGS] Buscando rankings paginados para Encounter ID {encounter_id} | Class: {user_class} | Spec: {user_spec} | Alvo Duração: {target_duration_sec}s"
        )

    # Query atualizada para aceitar páginas (page)
    query = """
    query GetRankingsCDs($encounterID: Int!, $className: String!, $specName: String!, $page: Int!) {
      worldData {
        encounter(id: $encounterID) {
          characterRankings(className: $className, specName: $specName, metric: dps, page: $page)
        }
      }
    }
    """
    clean_spec = (
        user_spec.replace(user_class, "").strip() if user_class in user_spec else user_spec.strip()
    )

    valid_refs, durations = [], []
    page = 1
    max_pages = (
        5  # Tenta buscar até 5 páginas (várias centenas de parses) para achar os 100 válidos
    )

    try:
        while page <= max_pages and len(valid_refs) < 100:
            variables = {
                "encounterID": encounter_id,
                "className": user_class.strip(),
                "specName": clean_spec,
                "page": page,
            }
            try:
                res_json = _wcl_client.query(query, variables, op_name="fetch_rankings_page")
            except ApiError as e:
                if DEBUG:
                    print(f"❌ [DEBUG RANKINGS] Erro na página {page}: {e}")
                break

            rankings_data = (
                res_json.get("data", {})
                .get("worldData", {})
                .get("encounter", {})
                .get("characterRankings", {})
            )
            rankings_list = rankings_data.get("rankings", [])

            if not rankings_list:
                if DEBUG:
                    print(f"🔎 [DEBUG RANKINGS] Fim dos rankings na página {page}.")
                break

            if DEBUG:
                print(
                    f"🔎 [DEBUG RANKINGS] Página {page}: {len(rankings_list)} logs retornados. Filtrando..."
                )

            for r in rankings_list:
                dur_sec = r.get("duration", 0) / 1000.0

                # Filtro de duração (Margem de ±30s ou a que você preferir)
                if abs(dur_sec - target_duration_sec) <= 30.0:
                    rep_info = r.get("report", {})
                    if rep_info.get("code") and rep_info.get("fightID"):
                        valid_refs.append(r)
                        durations.append(dur_sec)

            # Verifica se há mais páginas no WCL
            has_more = rankings_data.get("hasMorePages", False)
            if not has_more:
                break
            page += 1

        if DEBUG:
            print(
                f"🔎 [DEBUG RANKINGS] Total de logs válidos acumulados nas páginas: {len(valid_refs)}"
            )

        if not valid_refs:
            return [], 0, 0, 0, None

        # Pega até 100 referências válidas coletadas
        top_refs = valid_refs[:100]

        if DEBUG:
            print(
                f"🔎 [DEBUG THREADPOOL] Iniciando extração concorrente de timelines para os {len(top_refs)} logs de referência..."
            )

        reference_players = []
        with ThreadPoolExecutor(max_workers=5) as executor:
            tasks = [
                executor.submit(
                    fetch_player_timeline_data,
                    r["report"]["code"],
                    r["report"]["fightID"],
                    r["name"],
                )
                for r in top_refs
            ]
            for future in as_completed(tasks):
                ref_res = future.result()
                if ref_res:
                    reference_players.append(ref_res)

        if DEBUG:
            print(
                f"🔎 [DEBUG THREADPOOL] Timelines de referência coletadas com sucesso: {len(reference_players)}/{len(top_refs)}"
            )

        min_d = min(durations) if durations else target_duration_sec
        max_d = max(durations) if durations else target_duration_sec

        # T0.7 (achado 3.10): não fabricar mais "Parse méd" — characterRankings
        # não tem campo `percentile` (docs/schema_confirmado.md §8). Em seu
        # lugar, o DPS mediano da coorte é uma estatística real e disponível.
        ref_dps_values = [r["dps"] for r in reference_players if r.get("dps") is not None]
        cohort_median_dps = statistics.median(ref_dps_values) if ref_dps_values else None

        return reference_players, len(reference_players), min_d, max_d, cohort_median_dps
    except Exception as e:
        print(f"❌ [ERRO RANKINGS CDS]: {e}")
        return [], 0, 0, 0, None


def build_cd_reference_profile(reference_players):
    """T0.7: substitui a lógica antiga de avg_cd_duration/type (achado 3.3) —
    o perfil agora só acumula os dados brutos (presença, medianas de slot
    como ref_times, contagem de usos por jogador incluindo 0). Classificação
    MAJOR/MINOR e cooldown observado passam a viver em cadence.py, calculados
    sob demanda em discover_eligible_spell_ids/compare_all_spells.
    """
    num_logs = len(reference_players)
    if num_logs == 0:
        if DEBUG:
            print("⚠️ [DEBUG PROFILE] Nenhum jogador de referência para construir o perfil.")
        return {}

    if DEBUG:
        print(
            f"🔎 [DEBUG PROFILE] Construindo perfil estatístico com base em {num_logs} logs de referência..."
        )

    spell_accumulator = defaultdict(
        lambda: {"slots_timings": defaultdict(list), "presence_count": 0}
    )

    for ref in reference_players:
        seen_spells = set()
        for s_id, times in ref["timeline"].items():
            seen_spells.add(s_id)
            for idx, t in enumerate(times):
                spell_accumulator[s_id]["slots_timings"][idx].append(t)

        for s_id in seen_spells:
            spell_accumulator[s_id]["presence_count"] += 1

    profile = {}
    for s_id, data in spell_accumulator.items():
        presence = data["presence_count"] / num_logs

        all_slot_medians = []
        for slot_idx, times in sorted(data["slots_timings"].items()):
            if times:
                # Mediana em vez de média para evitar distorção por outliers.
                all_slot_medians.append(statistics.median(times))

        # Quantas vezes cada jogador de referência usou esta spell,
        # incluindo 0 para quem não usou — insumo do fallback de
        # classificação de cadence.py para spells de uso único.
        usage_counts = [len(ref["timeline"].get(s_id, [])) for ref in reference_players]
        n_usages_median = statistics.median(usage_counts) if usage_counts else 0.0

        profile[s_id] = {
            "presence": presence,
            "ref_times": sorted(all_slot_medians),
            "n_usages_median": n_usages_median,
        }

    if DEBUG:
        print(f"🔎 [DEBUG PROFILE] Perfil construído com {len(profile)} spells mapeadas.")
    return profile


################################################################################
# FILTRO E COMPARAÇÃO DE MAJOR/MINOR CDS (T0.7: usa cadence.py/comparison.py)
################################################################################
def discover_eligible_spell_ids(profile):
    eligible = []
    if DEBUG:
        print("\n🔎 [DEBUG FILTRO] Avaliando elegibilidade de cada spell no perfil de referência:")

    # Itera em ordem determinística de spell_id: `profile` é construído a
    # partir de reference_players coletados via ThreadPoolExecutor/
    # as_completed(), cuja ordem de conclusão (e portanto a ordem de
    # inserção das chaves no dict) não é determinística entre execuções.
    # Sem isso, empates de presença (comuns — muitas spells com presence
    # 100%) produziam uma ordem de relatório diferente a cada rodada.
    for s_id, stats in sorted(profile.items()):
        cadence = compute_cadence(stats["ref_times"], n_usages_median=stats["n_usages_median"])
        if is_eligible(s_id, stats["presence"], cadence, blacklist=MAJOR_CD_BLACKLIST):
            eligible.append((s_id, stats["presence"]))

    eligible.sort(key=lambda x: x[1], reverse=True)
    return [s_id for s_id, _ in eligible]


def compare_all_spells(user_data, profile, eligible_spell_ids, reference_n):
    """T0.7: substitui compare_major_cds_clean. Duas correções em relação ao
    legacy: (1) delega o pareamento para align() (T0.5) em vez do
    nearest-neighbor enviesado; (2) NÃO pula mais spells com user_times
    vazio (achado 3.1, segunda metade) — uma spell elegível que o jogador
    nunca usou agora aparece no relatório como o pior caso possível.
    """
    comparisons = []
    for s_id in eligible_spell_ids:
        stats = profile[s_id]
        spell_info = _spell_catalog.get(s_id)
        user_times = sorted(user_data["timeline"].get(s_id, []))
        comparison = compare_spell_usage(
            spell=spell_info,
            presence=stats["presence"],
            user_times=user_times,
            ref_times=stats["ref_times"],
            n_usages_median=stats["n_usages_median"],
            reference_n=reference_n,
        )
        comparisons.append(comparison)
    return comparisons


################################################################################
# PERCENTIL DO JOGADOR (T0.7, achado 3.11)
################################################################################
def fetch_player_percentile(
    report_code, fight_id, char_name, server, region, encounter_id, difficulty
):
    """Best-effort: retorna None (nunca um valor fabricado) se faltar
    qualquer dado necessário ou se a API falhar. characterRankings não tem
    percentil (docs/schema_confirmado.md §8); a fonte real é
    characterData.character.encounterRankings.ranks[].rankPercent (§9),
    casando pelo report.code + fightID desta análise.
    """
    if not server or not region or not difficulty:
        return None

    server_slug = server.lower().replace(" ", "-")
    query = """
    query GetPercentile($name: String!, $serverSlug: String!, $serverRegion: String!, $encounterID: Int!, $difficulty: Int!) {
      characterData {
        character(name: $name, serverSlug: $serverSlug, serverRegion: $serverRegion) {
          encounterRankings(encounterID: $encounterID, metric: dps, difficulty: $difficulty)
        }
      }
    }
    """
    try:
        res_json = _wcl_client.query(
            query,
            {
                "name": char_name,
                "serverSlug": server_slug,
                "serverRegion": region,
                "encounterID": encounter_id,
                "difficulty": difficulty,
            },
            op_name="fetch_player_percentile",
        )
    except ApiError as e:
        if DEBUG:
            print(f"❌ [DEBUG PERCENTIL] Erro ao buscar percentil: {e}")
        return None

    character = res_json.get("data", {}).get("characterData", {}).get("character")
    if not character:
        return None

    ranks = (character.get("encounterRankings") or {}).get("ranks") or []
    for rank in ranks:
        rep = rank.get("report", {})
        if rep.get("code") == report_code and rep.get("fightID") == fight_id:
            return rank.get("rankPercent")
    return None


################################################################################
# BOT DO DISCORD (COMANDOS ASSÍNCRONOS)
################################################################################
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)


@bot.event
async def on_ready():
    print(f"🤖 Bot conectado no Discord como {bot.user}")


@bot.command(name="analisar")
async def cmd_analisar(ctx, char_name: str, report_link: str):
    """Uso: !analisar NomeDoPlayer LinkDoWCL"""
    if DEBUG:
        print(
            f"\n📥 [DISCORD COMANDO] Recebido comando !analisar de {ctx.author} para o player '{char_name}' com o link: {report_link}"
        )

    code, fight_id = parse_report_input(report_link)

    if not fight_id:
        await ctx.send(
            "❌ Fight não encontrado no link (certifique-se de incluir `?fight=X` no link do WCL)."
        )
        return

    await ctx.send(
        f"🔍 Analisando **{char_name}** com até 100 logs de referência (Duração de kill compatível)..."
    )

    loop = asyncio.get_running_loop()

    def process_analysis():
        user_data = fetch_player_timeline_data(code, fight_id, char_name)
        if not user_data:
            return {"user_data": None}

        references, matched, min_d, max_d, cohort_median_dps = fetch_top_logs_for_cds(
            user_data["fight"]["encounter_id"],
            user_data["build"]["class"],
            user_data["build"]["spec"],
            user_data["fight"]["duration_sec"],
        )

        if not references:
            return {"user_data": user_data, "matched": 0}

        profile = build_cd_reference_profile(references)
        eligible_ids = discover_eligible_spell_ids(profile)
        comparisons = compare_all_spells(
            user_data, profile, eligible_ids, reference_n=len(references)
        )

        percentile = fetch_player_percentile(
            code,
            fight_id,
            char_name,
            user_data["build"].get("server"),
            user_data["build"].get("region"),
            user_data["fight"]["encounter_id"],
            user_data["fight"].get("difficulty"),
        )

        # T0.4/T0.7: persiste o catálogo de spells uma única vez, ao fim da
        # análise, na thread principal — nunca durante a coleta concorrente
        # (achado 4.1).
        _spell_catalog.flush()

        return {
            "user_data": user_data,
            "matched": matched,
            "min_d": min_d,
            "max_d": max_d,
            "cohort_median_dps": cohort_median_dps,
            "comparisons": comparisons,
            "percentile": percentile,
            "reference_n": len(references),
        }

    result = await loop.run_in_executor(None, process_analysis)

    if not result.get("user_data"):
        await ctx.send(
            f"❌ Jogador `{char_name}` não foi encontrado neste fight ou ocorreu um erro na busca."
        )
        return

    if not result.get("matched"):
        await ctx.send(
            "❌ Não foram encontradas referências compatíveis nos rankings com esse tempo de kill."
        )
        return

    user_data = result["user_data"]
    header = ReportHeader(
        char_name=char_name,
        boss_name=user_data["fight"]["boss_name"],
        class_name=user_data["build"]["class"],
        spec=user_data["build"]["spec"],
        reference_n=result["reference_n"],
        duration_min_s=result["min_d"],
        duration_max_s=result["max_d"],
        player_dps=user_data.get("dps"),
        player_percentile=result["percentile"],
        cohort_median_dps=result["cohort_median_dps"],
    )
    report_text = render_report(header, result["comparisons"])

    for chunk in chunk_report_for_discord(report_text, max_len=1900):
        await ctx.send(f"```markdown\n{chunk}\n```")


if __name__ == "__main__":
    if not DISCORD_TOKEN:
        print("❌ Token do Discord não encontrado no arquivo .env (DISCORD_TOKEN)")
    else:
        bot.run(DISCORD_TOKEN)
