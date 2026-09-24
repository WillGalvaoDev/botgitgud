"""T0.4 — thread-safe, append-only-in-memory spell name catalog.

Corrects three achados from the audit of the original bot:
- 3.8: `category` (trackable/non-trackable) is no longer persisted globally.
  That classification is a property of (spell, spec, encounter), not of the
  spell alone — persisting it made the legacy tool non-reproducible.
- 3.12: (indirectly) no lexical blacklist lives here; that's analysis-layer
  concern (T0.6), not the catalog's.
- 4.1: no more concurrent `open(path, "w")` from 5 worker threads. `learn()`
  only mutates memory under a lock; `flush()` persists once, atomically, on
  the caller's schedule (normally the main thread, once per analysis).
"""

from __future__ import annotations

import json
import shutil
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, get_args

import structlog

from botgitgud.blizzard.client import BlizzardClient
from botgitgud.domain.ability_identity import AbilityIdentity, IdentitySource

log = structlog.get_logger(__name__)

CATALOG_FILENAME = "spells.json"

SpellSource = Literal[
    "curated",
    "wcl_report_master_data",
    "wcl_table",
    "wcl_game_data",
    "blizzard_game_data",
    "legacy_catalog",
    "wcl",
    "blizzard",
    "unknown",
]
_VALID_SOURCES: tuple[str, ...] = get_args(SpellSource)


def _normalize_source(source: str) -> SpellSource:
    if source in _VALID_SOURCES:
        return source  # type: ignore[return-value]  # narrowed by the membership check above
    return "unknown"


@dataclass(frozen=True, slots=True)
class SpellInfo:
    spell_id: int
    name: str
    source: SpellSource


_PRECEDENCE = {
    IdentitySource.UNRESOLVED: 0,
    IdentitySource.LEGACY_CATALOG: 1,
    IdentitySource.BLIZZARD_GAME_DATA: 2,
    IdentitySource.WCL_GAME_DATA: 3,
    IdentitySource.WCL_TABLE: 4,
    IdentitySource.WCL_REPORT_MASTER_DATA: 5,
    IdentitySource.CURATED: 6,
}

_PERSISTED_TO_IDENTITY = {
    "curated": IdentitySource.CURATED,
    "wcl_report_master_data": IdentitySource.WCL_REPORT_MASTER_DATA,
    "wcl_table": IdentitySource.WCL_TABLE,
    "wcl_game_data": IdentitySource.WCL_GAME_DATA,
    "blizzard_game_data": IdentitySource.BLIZZARD_GAME_DATA,
    "legacy_catalog": IdentitySource.LEGACY_CATALOG,
    "wcl": IdentitySource.WCL_TABLE,
    "blizzard": IdentitySource.BLIZZARD_GAME_DATA,
    "unknown": IdentitySource.LEGACY_CATALOG,
}


class SpellCatalog:
    """Thread-safe, append-only spell name store (in memory; see module docstring)."""

    def __init__(self, path: Path, blizzard: BlizzardClient | None) -> None:
        self._path = path
        self._blizzard = blizzard
        self._lock = threading.Lock()
        self._entries: dict[int, SpellInfo] = {}
        self._identities: dict[int, AbilityIdentity] = {}
        self._load()

    def identity(self, spell_id: int) -> AbilityIdentity:
        """Resolve an ID while retaining source and structural status."""
        with self._lock:
            cached = self._identities.get(spell_id)
        if cached is not None:
            return cached

        return AbilityIdentity(spell_id, "", IdentitySource.UNRESOLVED, "unresolved")

    def resolve(self, spell_id: int) -> AbilityIdentity:
        """Public verb-form alias for callers resolving an ability identity."""
        return self.identity(spell_id)

    def get(self, spell_id: int) -> SpellInfo:
        with self._lock:
            cached = self._entries.get(spell_id)
        if cached is not None:
            return cached

        if self._blizzard is not None:
            name = self._blizzard.get_spell_name(spell_id)
            if name:
                self._store(SpellInfo(spell_id=spell_id, name=name, source="blizzard"))
                with self._lock:
                    return self._entries[spell_id]

        identity = self.identity(spell_id)
        if identity.resolution_status == "resolved":
            with self._lock:
                return self._entries[spell_id]

        return SpellInfo(
            spell_id=spell_id,
            name=f"Nome de spell não resolvido (ID: {spell_id})",
            source="unknown",
        )

    def learn(self, spell_id: int, name: str, source: str) -> None:
        if not name or name.startswith("Spell #"):
            return
        try:
            identity_source = IdentitySource(source)
        except ValueError:
            identity_source = _PERSISTED_TO_IDENTITY.get(source, IdentitySource.LEGACY_CATALOG)
        self._store_identity(
            AbilityIdentity(spell_id, name, identity_source, "resolved"),
            persisted_source=_normalize_source(source),
        )

    def flush(self) -> None:
        with self._lock:
            snapshot = dict(self._entries)

        payload = {
            str(spell_id): {"name": info.name, "source": info.source}
            for spell_id, info in sorted(snapshot.items())
        }

        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8"
        )
        tmp_path.replace(self._path)
        log.info("spell_catalog.flushed", n_entries=len(snapshot), path=str(self._path))

    # -- internal -------------------------------------------------------------

    def _store(self, info: SpellInfo) -> None:
        source = _PERSISTED_TO_IDENTITY.get(info.source, IdentitySource.LEGACY_CATALOG)
        self._store_identity(
            AbilityIdentity(info.spell_id, info.name, source, "resolved"),
            persisted_source=info.source,
        )

    def _store_identity(
        self, identity: AbilityIdentity, *, persisted_source: SpellSource | None = None
    ) -> None:
        with self._lock:
            current = self._identities.get(identity.canonical_id)
            if current is not None and (
                _PRECEDENCE[identity.identity_source] <= _PRECEDENCE[current.identity_source]
            ):
                return
            source = persisted_source or _normalize_source(identity.identity_source.value)
            self._identities[identity.canonical_id] = identity
            self._entries[identity.canonical_id] = SpellInfo(
                identity.canonical_id, identity.resolved_name, source
            )

    def _load(self) -> None:
        if not self._path.exists():
            return

        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            self._quarantine_corrupt_file(exc)
            return

        if not isinstance(raw, dict):
            self._quarantine_corrupt_file(TypeError(f"esperava um objeto JSON, achei {type(raw)}"))
            return

        loaded = 0
        for key, value in raw.items():
            try:
                spell_id = int(key)
            except (TypeError, ValueError):
                continue

            if isinstance(value, str):
                # Oldest format: {"123": "Spell Name"} — no source info at all.
                self._store(SpellInfo(spell_id=spell_id, name=value, source="unknown"))
                loaded += 1
            elif isinstance(value, dict):
                name = value.get("name")
                if not name:
                    continue
                # the pre-T0.4 category field is intentionally discarded here.
                source = _normalize_source(value.get("source", "unknown"))
                self._store(SpellInfo(spell_id=spell_id, name=name, source=source))
                loaded += 1

        log.info("spell_catalog.loaded", n_entries=loaded, path=str(self._path))

    def _quarantine_corrupt_file(self, exc: Exception) -> None:
        timestamp = int(time.time())
        corrupt_path = self._path.with_name(f"spells.corrupt.{timestamp}.json")
        quarantined = False
        try:
            self._path.replace(corrupt_path)
            quarantined = True
        except OSError as replace_exc:
            log.error(
                "spell_catalog.quarantine_failed",
                path=str(self._path),
                error=str(replace_exc),
            )

        log.error(
            "spell_catalog.corrupt_file",
            path=str(self._path),
            quarantined_to=str(corrupt_path) if quarantined else None,
            error=str(exc),
        )


# -- seed versionado vs cache de runtime (D-35) ----------------------------------
#
# O `spells.json` da raiz e um SEED versionado: entra no Git e serve de ponto de
# partida no primeiro boot. Producao NUNCA escreve nele.
#
# O cache mutavel de runtime vive em `settings.data_dir` — diretorio ja ignorado
# pelo Git —, e e o unico destino de learn()/flush(). Sem essa separacao, rodar
# o bot de verdade sujava um arquivo rastreado e quebrava os golden tests
# legados (incidente registrado em docs/operations.md).


def runtime_catalog_path(data_dir: Path) -> Path:
    """Onde o catalogo mutavel vive em producao."""
    return data_dir / CATALOG_FILENAME


def open_runtime_catalog(
    data_dir: Path,
    *,
    blizzard: BlizzardClient | None,
    seed_path: Path | None = None,
) -> SpellCatalog:
    """Abre o catalogo de producao apontando para o cache de runtime.

    Primeiro boot sem cache: copia o seed versionado, se existir, para nao
    perder os nomes ja conhecidos. O seed e aberto somente para leitura — a
    copia e o unico contato com ele, e toda escrita posterior cai no cache.
    Um seed ausente (instalacao empacotada, por exemplo) apenas inicia o
    catalogo vazio; nunca e erro.
    """
    runtime = runtime_catalog_path(data_dir)
    if not runtime.exists() and seed_path is not None and seed_path.is_file():
        runtime.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(seed_path, runtime)
        log.info("spell_catalog.seeded_runtime_cache", seed=str(seed_path), runtime=str(runtime))
    return SpellCatalog(runtime, blizzard=blizzard)
