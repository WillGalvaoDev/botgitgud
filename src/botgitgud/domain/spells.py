"""T0.4 — thread-safe, append-only-in-memory spell name catalog.

Corrects three achados from docs/relario.md:
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
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, get_args

import structlog

from botgitgud.blizzard.client import BlizzardClient

log = structlog.get_logger(__name__)

SpellSource = Literal["wcl", "blizzard", "unknown"]
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


class SpellCatalog:
    """Thread-safe, append-only spell name store (in memory; see module docstring)."""

    def __init__(self, path: Path, blizzard: BlizzardClient | None) -> None:
        self._path = path
        self._blizzard = blizzard
        self._lock = threading.Lock()
        self._entries: dict[int, SpellInfo] = {}
        self._load()

    def get(self, spell_id: int) -> SpellInfo:
        with self._lock:
            cached = self._entries.get(spell_id)
        if cached is not None:
            return cached

        if self._blizzard is not None:
            name = self._blizzard.get_spell_name(spell_id)
            if name:
                info = SpellInfo(spell_id=spell_id, name=name, source="blizzard")
                self._store(info)
                return info

        return SpellInfo(spell_id=spell_id, name=f"Spell #{spell_id}", source="unknown")

    def learn(self, spell_id: int, name: str, source: str) -> None:
        if not name or name.startswith("Spell #"):
            return
        self._store(SpellInfo(spell_id=spell_id, name=name, source=_normalize_source(source)))

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
        with self._lock:
            self._entries[info.spell_id] = info

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
                self._entries[spell_id] = SpellInfo(spell_id=spell_id, name=value, source="unknown")
                loaded += 1
            elif isinstance(value, dict):
                name = value.get("name")
                if not name:
                    continue
                # the pre-T0.4 category field is intentionally discarded here.
                source = _normalize_source(value.get("source", "unknown"))
                self._entries[spell_id] = SpellInfo(spell_id=spell_id, name=name, source=source)
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
