"""tests/fixtures/record.py — records live WCL/Blizzard HTTP traffic as cassettes.

Run manually whenever the fixture log needs to be (re)captured:

    python tests/fixtures/record.py

NEVER invoked by pytest — this is the writer side; tests/conftest.py's
`mock_http` fixture is the reader side (see http_cassette.py for the shared
key format both sides must agree on). Requires real WCL/Blizzard credentials
in .env at the repo root.

Runs the legacy pipeline in an isolated CWD (tests/fixtures/legacy_runner.py)
so it never mutates the git-tracked spells.json (docs/desvios.md D-4).
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from http_cassette import Cassette, redact_headers, save_cassette
from legacy_runner import isolated_legacy_bot

FIXTURE_REPORT_CODE = "PtfBbQKRY9d6zAMC"
FIXTURE_FIGHT_ID = 1
FIXTURE_CHARACTER = "Zarad"
MAX_REFERENCE_LOGS = 5  # keep cassette count small; see T0.2 instructions

RECORDING_SCRATCH_DIR = Path(__file__).resolve().parent / "_record_scratch"


class _RecordingHooks:
    """Wraps requests.post/get, saving every real response as a cassette."""

    def __init__(self) -> None:
        self._real_post = requests.post
        self._real_get = requests.get
        self.recorded = 0

    def install(self) -> None:
        requests.post = self._post  # type: ignore[method-assign]
        requests.get = self._get  # type: ignore[method-assign]

    def uninstall(self) -> None:
        requests.post = self._real_post  # type: ignore[method-assign]
        requests.get = self._real_get  # type: ignore[method-assign]

    def _record(
        self,
        method: str,
        url: str,
        payload: dict[str, Any] | None,
        headers: dict[str, str] | None,
        response: requests.Response,
    ) -> None:
        try:
            body = response.json()
        except ValueError:
            body = None
        path = save_cassette(
            Cassette(
                method=method,
                url=url,
                request_payload=payload,
                request_headers=redact_headers(headers),
                status_code=response.status_code,
                response_json=body,
            )
        )
        self.recorded += 1
        print(f"  gravado: {path.name}  ({method} {url})")

    def _post(
        self,
        url: str,
        *,
        data: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        auth: tuple[str, str] | None = None,
        timeout: float | None = None,
        **kw: Any,
    ) -> requests.Response:
        response = self._real_post(
            url, data=data, json=json, headers=headers, auth=auth, timeout=timeout, **kw
        )
        self._record("POST", url, json if json is not None else data, headers, response)
        return response

    def _get(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
        timeout: float | None = None,
        **kw: Any,
    ) -> requests.Response:
        response = self._real_get(url, headers=headers, params=params, timeout=timeout, **kw)
        self._record("GET", url, params, headers, response)
        return response


def main() -> int:
    hooks = _RecordingHooks()
    with isolated_legacy_bot(RECORDING_SCRATCH_DIR) as legacy_bot:
        hooks.install()
        try:
            token = legacy_bot.get_wcl_token()
            if not token:
                print("ERRO: falha ao obter token WCL — verifique .env na raiz do repo")
                return 1

            user_data = legacy_bot.fetch_player_timeline_data(
                token, FIXTURE_REPORT_CODE, FIXTURE_FIGHT_ID, FIXTURE_CHARACTER
            )
            if not user_data:
                print(f"ERRO: jogador '{FIXTURE_CHARACTER}' não encontrado no fight de fixture")
                return 1

            references, matched, min_d, max_d, avg_p, _parses, min_p, max_p = (
                legacy_bot.fetch_top_logs_for_cds(
                    token,
                    user_data["fight"]["encounter_id"],
                    user_data["build"]["class"],
                    user_data["build"]["spec"],
                    user_data["fight"]["duration_sec"],
                )
            )
            print(f"\nReferências capturadas: {matched}")
            if matched > MAX_REFERENCE_LOGS:
                print(
                    f"AVISO: {matched} referências > limite documentado de {MAX_REFERENCE_LOGS}. "
                    "O pool real de rankings para este encontro/spec é pequeno (ver "
                    "docs/schema_confirmado.md §8), então isso não era esperado — investigue "
                    "antes de prosseguir; não edite este script para simplesmente elevar o limite."
                )

            # Espelha o pipeline COMPLETO (igual a cmd_analisar/process_analysis em
            # legacy/bot.py), não só até fetch_top_logs_for_cds: build_cd_reference_profile
            # chama get_spell_data() por spell_id, que pode cair no fallback da API da
            # Blizzard para spells ausentes do castsTable do WCL — essas chamadas também
            # precisam de cassete, senão o golden test falha ao rodar o pipeline inteiro.
            profile = legacy_bot.build_cd_reference_profile(references)
            major_cds = legacy_bot.discover_clean_major_cds(profile)
            comparison = legacy_bot.compare_major_cds_clean(user_data, profile, major_cds)
            legacy_bot.generate_coach_report_string(
                FIXTURE_CHARACTER, user_data, matched, min_d, max_d, avg_p, min_p, max_p, comparison
            )
        finally:
            hooks.uninstall()

    print(f"\n{hooks.recorded} cassetes gravados em tests/fixtures/cassettes/")

    cassettes_dir = Path(__file__).resolve().parent / "cassettes"
    # "Bearer " catches leaked request headers; "eyJ" catches a raw JWT access_token
    # that slipped into a response body without going through redact_response_body
    # (see docs/desvios.md D-6 — the OAuth token endpoint returns the real credential
    # in its JSON body, not just in a header).
    leak_markers = ("Bearer ", "eyJ")
    leaked = [
        f
        for f in cassettes_dir.glob("*.json")
        if any(marker in f.read_text(encoding="utf-8") for marker in leak_markers)
    ]
    if leaked:
        print(f"ERRO DE SEGURANÇA: segredo vazado em {[f.name for f in leaked]}")
        return 1

    print("Verificação de segurança OK: nenhum cassete contém segredos ('Bearer '/'eyJ').")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
