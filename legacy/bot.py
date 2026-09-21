import os
import re
import json
import statistics
import requests
import asyncio
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dotenv import load_dotenv
import discord
from discord.ext import commands

load_dotenv()

WCL_CLIENT_ID = os.getenv("WCL_CLIENT_ID")
WCL_CLIENT_SECRET = os.getenv("WCL_CLIENT_SECRET")
BLIZZARD_CLIENT_ID = os.getenv("BLIZZARD_CLIENT_ID")
BLIZZARD_CLIENT_SECRET = os.getenv("BLIZZARD_CLIENT_SECRET")
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")

API_URL = "https://www.warcraftlogs.com/api/v2/client"
HEADERS_BASE = {"Content-Type": "application/json", "Accept-Language": "en-US"}

SPELLS_FILE = "spells.json"
SPELL_NAME_CACHE = {}
BLIZZARD_TOKEN_CACHE = None
DEBUG = True

MAJOR_CD_BLACKLIST = {
    22812,  # Barkskin
}

if os.path.exists(SPELLS_FILE):
    try:
        with open(SPELLS_FILE, "r", encoding="utf-8") as f:
            raw_db = json.load(f)
            LOCAL_SPELL_DB = {}
            for k, v in raw_db.items():
                if isinstance(v, str):
                    LOCAL_SPELL_DB[k] = {"name": v, "category": "trackable", "source": "migrated"}
                elif isinstance(v, dict):
                    LOCAL_SPELL_DB[k] = v
        if DEBUG:
            print(f"🔎 [DEBUG JSON] Carregado {len(LOCAL_SPELL_DB)} spells de {SPELLS_FILE}")
    except Exception as e:
        if DEBUG:
            print(f"❌ [DEBUG JSON] Erro ao ler {SPELLS_FILE}: {e}")
        LOCAL_SPELL_DB = {}
else:
    if DEBUG:
        print(f"🔎 [DEBUG JSON] Arquivo {SPELLS_FILE} não encontrado. Iniciando vazio.")
    LOCAL_SPELL_DB = {}


################################################################################
# BLIZZARD OAUTH & SPELL RESOLVER HÍBRIDO
################################################################################
def get_blizzard_token():
    global BLIZZARD_TOKEN_CACHE
    if BLIZZARD_TOKEN_CACHE:
        return BLIZZARD_TOKEN_CACHE

    url = "https://oauth.battle.net/token"
    try:
        res = requests.post(
            url,
            data={"grant_type": "client_credentials"},
            auth=(BLIZZARD_CLIENT_ID, BLIZZARD_CLIENT_SECRET)
        )
        if res.status_code == 200:
            BLIZZARD_TOKEN_CACHE = res.json().get("access_token")
            return BLIZZARD_TOKEN_CACHE
    except Exception as e:
        print(f"❌ [ERRO OAUTH BLIZZARD]: {e}")
    return None

def fetch_spell_from_blizzard(spell_id):
    token = get_blizzard_token()
    if not token:
        return None

    url = f"https://us.api.blizzard.com/data/wow/spell/{spell_id}"
    headers = {"Authorization": f"Bearer {token}"}
    params = {"namespace": "static-us", "locale": "en_US"}

    try:
        res = requests.get(url, headers=headers, params=params, timeout=3)
        if res.status_code == 200:
            data = res.json()
            spell_name = data.get("name")
            if isinstance(spell_name, dict):
                spell_name = spell_name.get("en_US")
            
            if spell_name:
                return {
                    "name": spell_name,
                    "category": "trackable",
                    "source": "blizzard"
                }
    except Exception as e:
        if DEBUG:
            print(f"❌ [DEBUG BLIZZARD SPELL] Erro na requisição para ID {spell_id}: {e}")

    return None

def get_spell_data(spell_id):
    s_id = str(spell_id)

    if spell_id in SPELL_NAME_CACHE:
        return SPELL_NAME_CACHE[spell_id]

    if s_id in LOCAL_SPELL_DB:
        data = LOCAL_SPELL_DB[s_id]
        SPELL_NAME_CACHE[spell_id] = data
        return data

    data = fetch_spell_from_blizzard(spell_id)
    if data:
        LOCAL_SPELL_DB[s_id] = data
        try:
            with open(SPELLS_FILE, "w", encoding="utf-8") as f:
                json.dump(LOCAL_SPELL_DB, f, indent=4, ensure_ascii=False)
        except Exception as e:
            print(f"❌ [ERRO AO SALVAR SPELL NO JSON]: {e}")

        SPELL_NAME_CACHE[spell_id] = data
        return data

    fallback_data = {
        "name": f"Spell #{spell_id}",
        "category": "trackable",
        "source": "unknown"
    }
    return fallback_data

def save_spell_to_local_db(spell_id: int, name: str, source: str = "wcl"):
    global LOCAL_SPELL_DB
    s_id_str = str(spell_id)
    if name and not name.startswith("Spell #") and s_id_str not in LOCAL_SPELL_DB:
        LOCAL_SPELL_DB[s_id_str] = {
            "name": name,
            "category": "trackable",
            "source": source
        }
        try:
            with open(SPELLS_FILE, "w", encoding="utf-8") as f:
                json.dump(LOCAL_SPELL_DB, f, ensure_ascii=False, indent=4)
        except Exception as e:
            print(f"❌ [ERRO AO SALVAR SPELL NO JSON]: {e}")


################################################################################
# OAUTH WCL & UTILS
################################################################################
def get_wcl_token():
    url = "https://www.warcraftlogs.com/oauth/token"
    try:
        res = requests.post(
            url,
            data={"grant_type": "client_credentials"},
            auth=(WCL_CLIENT_ID, WCL_CLIENT_SECRET)
        )
        if res.status_code == 200:
            return res.json().get("access_token")
    except Exception as e:
        print(f"❌ [ERRO OAUTH WCL]: {e}")
    return None

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
def fetch_player_timeline_data(token, report_code, fight_id, char_name):
    headers = {**HEADERS_BASE, "Authorization": f"Bearer {token}"}
    
    query_meta = """
    query GetPlayerMeta($code: String!, $fightIDs: [Int]!) {
      reportData {
        report(code: $code) {
          fights(fightIDs: $fightIDs) {
            id encounterID name startTime endTime kill
          }
          table(fightIDs: $fightIDs, dataType: Summary, translate: true)
          castsTable: table(fightIDs: $fightIDs, dataType: Casts, translate: true)
        }
      }
    }
    """
    try:
        res = requests.post(API_URL, json={"query": query_meta, "variables": {"code": report_code, "fightIDs": [fight_id]}}, headers=headers)
        if res.status_code != 200:
            return None
        
        data = res.json().get("data", {}).get("reportData", {}).get("report", {})
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
        for role_group in ["dps", "tanks", "healers"]:
            for p in player_details.get(role_group, []):
                if p.get("name", "").lower() == char_name.lower():
                    player_id = p.get("id")
                    user_class = p.get("type", "Unknown")
                    specs = p.get("specs", [])
                    user_spec = specs[0].get("spec", "Unknown") if specs and isinstance(specs[0], dict) else (specs[0] if specs else "Unknown")
                    break

        if not player_id:
            return None

        casts_entries = data.get("castsTable", {}).get("data", {}).get("entries", [])
        for entry in casts_entries:
            if entry.get("id") == player_id:
                for ab in entry.get("abilities", []):
                    guid = ab.get("guid") or ab.get("id")
                    name = ab.get("name")
                    if guid and name:
                        s_id = int(guid)
                        save_spell_to_local_db(s_id, name, source="wcl")

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
            ev_res = requests.post(API_URL, json={
                "query": query_events,
                "variables": {
                    "code": report_code,
                    "fightIDs": [fight_id],
                    "startTime": current_start,
                    "endTime": end_time_ms
                }
            }, headers=headers)

            if ev_res.status_code != 200:
                break

            ev_json = ev_res.json().get("data", {}).get("reportData", {}).get("report", {}).get("events", {})
            events_data = ev_json.get("data", [])
            
            for ev in events_data:
                if ev.get("sourceID") == player_id and ev.get("type") == "cast":
                    spell_id = ev.get("abilityGameID") or ev.get("ability")
                    if not spell_id:
                        continue
                    
                    s_id = int(spell_id)
                    rel_sec = round((ev.get("timestamp", start_time_ms) - start_time_ms) / 1000.0, 1)
                    timeline_by_id[s_id].append(rel_sec)

            next_page = ev_json.get("nextPageTimestamp")
            if not next_page or next_page <= current_start or next_page >= end_time_ms:
                break
            current_start = next_page

        return {
            "fight": {
                "fight_id": fight_id,
                "encounter_id": selected_fight["encounterID"],
                "boss_name": selected_fight["name"],
                "duration_sec": duration_sec,
            },
            "build": {
                "class": user_class,
                "spec": user_spec
            },
            "timeline": timeline_by_id
        }
    except Exception as e:
        print(f"❌ [ERRO FETCH TIMELINE]: {e}")
        return None


################################################################################
# PERFIL DE REFERÊNCIA DE MAJOR CDS (Filtro ajustado para 100+ logs e duração do player + 30s)
################################################################################
def fetch_top_logs_for_cds(token, encounter_id, user_class, user_spec, target_duration_sec):
    if DEBUG:
        print(f"🔎 [DEBUG RANKINGS] Buscando rankings paginados para Encounter ID {encounter_id} | Class: {user_class} | Spec: {user_spec} | Alvo Duração: {target_duration_sec}s")
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {token}"}
    
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
    clean_spec = user_spec.replace(user_class, "").strip() if user_class in user_spec else user_spec.strip()
    
    valid_refs, durations, parses = [], [], []
    page = 1
    max_pages = 5  # Tenta buscar até 5 páginas (várias centenas de parses) para achar os 100 válidos

    try:
        while page <= max_pages and len(valid_refs) < 100:
            variables = {"encounterID": encounter_id, "className": user_class.strip(), "specName": clean_spec, "page": page}
            res = requests.post(API_URL, json={"query": query, "variables": variables}, headers=headers)
            
            if res.status_code != 200:
                if DEBUG:
                    print(f"❌ [DEBUG RANKINGS] Erro HTTP na página {page}: {res.text}")
                break

            rankings_data = res.json().get("data", {}).get("worldData", {}).get("encounter", {}).get("characterRankings", {})
            rankings_list = rankings_data.get("rankings", [])
            
            if not rankings_list:
                if DEBUG:
                    print(f"🔎 [DEBUG RANKINGS] Fim dos rankings na página {page}.")
                break

            if DEBUG:
                print(f"🔎 [DEBUG RANKINGS] Página {page}: {len(rankings_list)} logs retornados. Filtrando...")

            for r in rankings_list:
                dur_sec = r.get("duration", 0) / 1000.0
                
                # Filtro de duração (Margem de ±30s ou a que você preferir)
                if abs(dur_sec - target_duration_sec) <= 30.0:
                    rep_info = r.get("report", {})
                    if rep_info.get("code") and rep_info.get("fightID"):
                        p_val = r.get("percentile", 99.0)
                        r["_extracted_meta"] = {
                            "parse": p_val,
                            "duration": dur_sec,
                            "player": r.get("name", "Unknown")
                        }
                        valid_refs.append(r)
                        durations.append(dur_sec)
                        parses.append(p_val)

            # Verifica se há mais páginas no WCL
            has_more = rankings_data.get("hasMorePages", False)
            if not has_more:
                break
            page += 1

        if DEBUG:
            print(f"🔎 [DEBUG RANKINGS] Total de logs válidos acumulados nas páginas: {len(valid_refs)}")

        if not valid_refs:
            return [], 0, 0, 0, 0.0, [], 0, 0

        # Pega até 100 referências válidas coletadas
        top_refs = valid_refs[:100]

        if DEBUG:
            print(f"🔎 [DEBUG THREADPOOL] Iniciando extração concorrente de timelines para os {len(top_refs)} logs de referência...")

        reference_players = []
        with ThreadPoolExecutor(max_workers=5) as executor:
            tasks = [executor.submit(fetch_player_timeline_data, token, r["report"]["code"], r["report"]["fightID"], r["name"]) for r in top_refs]
            for future in as_completed(tasks):
                ref_res = future.result()
                if ref_res:
                    reference_players.append(ref_res)

        if DEBUG:
            print(f"🔎 [DEBUG THREADPOOL] Timelines de referência coletadas com sucesso: {len(reference_players)}/{len(top_refs)}")

        min_d = min(durations) if durations else target_duration_sec
        max_d = max(durations) if durations else target_duration_sec
        min_p = min(parses) if parses else 99.0
        max_p = max(parses) if parses else 99.0
        avg_p = sum(parses) / len(parses) if parses else 99.0

        return reference_players, len(reference_players), min_d, max_d, avg_p, parses, min_p, max_p
    except Exception as e:
        print(f"❌ [ERRO RANKINGS CDS]: {e}")
        return [], 0, 0, 0, 0.0, [], 0, 0

def build_cd_reference_profile(reference_players):
    num_logs = len(reference_players)
    if num_logs == 0:
        if DEBUG:
            print("⚠️ [DEBUG PROFILE] Nenhum jogador de referência para construir o perfil.")
        return {}

    if DEBUG:
        print(f"🔎 [DEBUG PROFILE] Construindo perfil estatístico com base em {num_logs} logs de referência...")

    spell_accumulator = defaultdict(lambda: {
        "slots_timings": defaultdict(list),
        "presence_count": 0
    })

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
        spell_info = get_spell_data(s_id)
        name = spell_info["name"]
        category = spell_info.get("category", "trackable")
        source = spell_info.get("source", "unknown")
        
        presence = data["presence_count"] / num_logs
        
        slot_timings_data = {}
        all_slot_medians = []

        if DEBUG:
            print(f"\n📋 [DEBUG RAW SAMPLES] Amostras de tempos brutos para a spell: '{name}' (ID: {s_id})")

        for slot_idx, times in sorted(data["slots_timings"].items()):
            if times:
                # SUBSTITUIÇÃO: Usando a Mediana Estatística em vez da Média Aritmética para evitar distorções de outliers
                s_median = statistics.median(times)
                s_std = statistics.stdev(times) if len(times) > 1 else 1.0
                slot_timings_data[slot_idx] = {
                    "avg": round(s_median, 1),
                    "std": round(s_std, 1),
                    "raw": times
                }
                all_slot_medians.append(s_median)
                if DEBUG:
                    print(f"   -> Slot/Uso #{slot_idx + 1} | Usos reportados por {len(times)} jogadores | Tempos brutos (segundos): {times} | **Mediana:** {round(s_median, 1)}s")

        avg_cd_duration = 0
        if len(all_slot_medians) > 1:
            intervals = [all_slot_medians[i+1] - all_slot_medians[i] for i in range(len(all_slot_medians)-1)]
            slot_intervals_std = statistics.stdev(intervals) if len(intervals) > 1 else 0.0
            avg_cd_duration = statistics.mean(intervals) if intervals else 0
        else:
            slot_intervals_std = 0.0
            avg_cd_duration = all_slot_medians[0] if all_slot_medians else 0

        cd_type = "MAJOR" if avg_cd_duration >= 120 else "MINOR"

        profile[s_id] = {
            "id": s_id,
            "name": name,
            "category": category,
            "source": source,
            "presence": presence,
            "avg_cd_duration": avg_cd_duration,
            "slot_timings": slot_timings_data,
            "all_cast_times": sorted(all_slot_medians),
            "std_intervals": slot_intervals_std,
            "type": cd_type
        }

    if DEBUG:
        print(f"🔎 [DEBUG PROFILE] Perfil construído com {len(profile)} spells mapeadas.")
    return profile


################################################################################
# FILTRO DE MAJOR CDS
################################################################################
def discover_clean_major_cds(profile):
    cds = []
    global LOCAL_SPELL_DB
    updated_json = False

    if DEBUG:
        print("\n🔎 [DEBUG FILTRO] Avaliando elegibilidade de cada spell no perfil de referência:")

    for s_id, stats in profile.items():
        s_id_str = str(s_id)
        name = stats["name"]
        presence = stats["presence"]
        avg_cd = stats["avg_cd_duration"]
        source = stats.get("source", "unknown")
        current_category = stats.get("category", "trackable")

        if current_category == "non-trackable":
            continue

        if s_id in MAJOR_CD_BLACKLIST or source == "unknown":
            if s_id_str in LOCAL_SPELL_DB and LOCAL_SPELL_DB[s_id_str]["category"] != "non-trackable":
                LOCAL_SPELL_DB[s_id_str]["category"] = "non-trackable"
                updated_json = True
            continue

        name_lower = name.lower()
        if any(x in name_lower for x in ["potion", "healthstone", "gladiator", "ring", "trinket"]):
            if s_id_str in LOCAL_SPELL_DB and LOCAL_SPELL_DB[s_id_str]["category"] != "non-trackable":
                LOCAL_SPELL_DB[s_id_str]["category"] = "non-trackable"
                updated_json = True
            continue

        if presence >= 0.70 and avg_cd >= 18.0:
            cds.append((s_id, presence))
        else:
            if s_id_str in LOCAL_SPELL_DB and LOCAL_SPELL_DB[s_id_str]["category"] != "non-trackable":
                LOCAL_SPELL_DB[s_id_str]["category"] = "non-trackable"
                updated_json = True

    if updated_json:
        try:
            with open(SPELLS_FILE, "w", encoding="utf-8") as f:
                json.dump(LOCAL_SPELL_DB, f, ensure_ascii=False, indent=4)
        except Exception as e:
            print(f"❌ [ERRO AO ATUALIZAR SPELLS NO JSON]: {e}")

    # Ordem canonica: presenca DESC + spell_id ASC como desempate de identidade.
    # `reference_players` chega na ordem de conclusao das threads (as_completed
    # em fetch_top_logs_for_cds), e essa ordem se propaga ate as chaves de
    # `profile`. Um sort ESTAVEL apenas por presenca herdava o escalonamento do
    # ThreadPoolExecutor sempre que duas spells empatavam, produzindo dois
    # relatorios distintos para a MESMA entrada. O desempate por spell_id nao
    # muda nenhum valor, nota, elegibilidade ou selecao — so torna a ordem de
    # apresentacao reproduzivel. Ver docs/v1-readiness-determinism.md.
    cds.sort(key=lambda x: (-x[1], x[0]))
    return [item[0] for item in cds]

def compare_major_cds_clean(user_data, profile, major_cd_ids):
    comparisons = []

    for s_id in major_cd_ids:
        stats = profile[s_id]
        
        if stats.get("category") == "non-trackable":
            continue

        spell_name = stats["name"]
        cd_type = stats["type"]
        avg_duration = stats["avg_cd_duration"]
        presence = stats["presence"]
        
        user_times = user_data["timeline"].get(s_id, [])
        if not user_times:
            continue

        possible_refs = stats["all_cast_times"]
        if not possible_refs:
            continue

        slot_diffs = []

        for idx, u_time in enumerate(user_times):
            if idx < len(possible_refs):
                ref_time = min(
                    possible_refs,
                    key=lambda x: abs(x - u_time)
                )
                delta = round(u_time - ref_time, 1)
                abs_d = abs(delta)

                if abs_d <= 10.0:
                    status = "🟢"
                elif abs_d <= 25.0:
                    status = "🟡"
                else:
                    status = "🔴"

                slot_diffs.append({
                    "usage": idx + 1,
                    "delta": delta,
                    "status": status,
                    "user_time": u_time,
                    "ref_time": ref_time,
                    "excedente": False
                })
            else:
                last_ref = possible_refs[-1]
                extra_steps = idx - len(possible_refs) + 1
                projected_ref = round(last_ref + (extra_steps * avg_duration), 1)

                slot_diffs.append({
                    "usage": idx + 1,
                    "delta": 0.0,
                    "status": "⚪",
                    "user_time": u_time,
                    "ref_time": projected_ref,
                    "excedente": True
                })

        if slot_diffs:
            comparisons.append({
                "spell_name": spell_name,
                "type": cd_type,
                "avg_cd": int(avg_duration),
                "presence": int(presence * 100),
                "slots": slot_diffs
            })

    return comparisons


################################################################################
# GERAÇÃO DO TEXTO DO RELATÓRIO
################################################################################
def generate_coach_report_string(char_name, user_data, matched, min_d, max_d, avg_p, min_p, max_p, comparisons):
    if DEBUG:
        print("🔎 [DEBUG RELATÓRIO] Formatando string de relatório para envio...")
    min_fmt = f"{int(min_d//60)}m{int(min_d%60):02d}s"
    max_fmt = f"{int(max_d//60)}m{int(max_d%60):02d}s"

    report = []
    report.append("=" * 42)
    report.append("GITGUD MAJOR CD ANALYSIS")
    report.append("=" * 42)
    report.append(f"**Player:** {char_name}")
    report.append(f"**Boss:** {user_data['fight']['boss_name']}")
    report.append(f"**Spec:** {user_data['build']['spec']} {user_data['build']['class']}")
    report.append(f"**Reference:** {matched} logs | Parse méd: {int(avg_p)} (min {int(min_p)} - max {int(max_p)}) | Duração: {min_fmt} - {max_fmt}")
    report.append("=" * 42)

    if not comparisons:
        report.append("\n⚡ Nenhum Major/Minor CD elegível encontrado.")
        report.append("=" * 42)
        return "\n".join(report)

    major_cds = [c for c in comparisons if c["type"] == "MAJOR"]
    minor_cds = [c for c in comparisons if c["type"] == "MINOR"]

    if major_cds:
        report.append("\n🔥 **OFFENSIVE MAJOR CDS**")
        report.append("-" * 42)
        for cd in major_cds:
            report.append(f"\n**{cd['spell_name']}** (CD: {cd['avg_cd']}s | Pres: {cd['presence']}%)")
            report.append("-" * 30)
            for slot in cd["slots"]:
                if slot["excedente"]:
                    report.append(f"Uso #{slot['usage']} | Player: {slot['user_time']}s | *excedente*")
                else:
                    d_val = round(slot['delta'], 1)
                    d_sign = f"+{d_val}s" if d_val > 0 else f"{d_val}s"
                    ref_val = round(slot['ref_time'], 1)
                    report.append(f"Uso #{slot['usage']} | Player: {slot['user_time']}s | Ideal: {ref_val}s | Delta: {d_sign} {slot['status']}")

    if minor_cds:
        report.append("\n⚡ **MINOR CDS / BURST UTILITIES**")
        report.append("-" * 42)
        for cd in minor_cds:
            report.append(f"\n**{cd['spell_name']}** (CD: {cd['avg_cd']}s | Pres: {cd['presence']}%)")
            report.append("-" * 30)
            for slot in cd["slots"]:
                if slot["excedente"]:
                    report.append(f"Uso #{slot['usage']} | Player: {slot['user_time']}s | *excedente*")
                else:
                    d_val = round(slot['delta'], 1)
                    d_sign = f"+{d_val}s" if d_val > 0 else f"{d_val}s"
                    ref_val = round(slot['ref_time'], 1)
                    report.append(f"Uso #{slot['usage']} | Player: {slot['user_time']}s | Ideal: {ref_val}s | Delta: {d_sign} {slot['status']}")

    report.append("\n" + "=" * 42)
    return "\n".join(report)


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
        print(f"\n📥 [DISCORD COMANDO] Recebido comando !analisar de {ctx.author} para o player '{char_name}' com o link: {report_link}")

    code, fight_id = parse_report_input(report_link)

    if not fight_id:
        await ctx.send("❌ Fight não encontrado no link (certifique-se de incluir `?fight=X` no link do WCL).")
        return

    token = get_wcl_token()
    if not token:
        await ctx.send("❌ Falha ao obter token de acesso da API do Warcraft Logs.")
        return

    await ctx.send(f"🔍 Analisando **{char_name}** com até 100 logs de referência (Duração de kill compatível)...")

    loop = asyncio.get_running_loop()
    
    def process_analysis():
        user_data = fetch_player_timeline_data(token, code, fight_id, char_name)
        if not user_data:
            return None, None, None, None, None, None, None, None, None

        references, matched, min_d, max_d, avg_p, parses, min_p, max_p = fetch_top_logs_for_cds(
            token,
            user_data["fight"]["encounter_id"],
            user_data["build"]["class"],
            user_data["build"]["spec"],
            user_data["fight"]["duration_sec"]
        )

        if not references:
            return user_data, None, 0, 0, 0, 0, 0, 0, 0

        profile = build_cd_reference_profile(references)
        major_cds = discover_clean_major_cds(profile)
        comparison = compare_major_cds_clean(user_data, profile, major_cds)
        return user_data, matched, min_d, max_d, avg_p, parses, min_p, max_p, comparison

    user_data, matched, min_d, max_d, avg_p, parses, min_p, max_p, comparison = await loop.run_in_executor(
        None, process_analysis
    )

    if not user_data:
        await ctx.send(f"❌ Jogador `{char_name}` não foi encontrado neste fight ou ocorreu um erro na busca.")
        return

    if not matched:
        await ctx.send("❌ Não foram encontradas referências compatíveis nos rankings com esse tempo de kill.")
        return

    report_text = generate_coach_report_string(
        char_name, user_data, matched, min_d, max_d, avg_p, min_p, max_p, comparison
    )

    if len(report_text) > 1900:
        chunks = [report_text[i:i+1900] for i in range(0, len(report_text), 1900)]
        for chunk in chunks:
            await ctx.send(f"```markdown\n{chunk}\n```")
    else:
        await ctx.send(f"```markdown\n{report_text}\n```")


if __name__ == "__main__":
    if not DISCORD_TOKEN:
        print("❌ Token do Discord não encontrado no arquivo .env (DISCORD_TOKEN)")
    else:
        bot.run(DISCORD_TOKEN)