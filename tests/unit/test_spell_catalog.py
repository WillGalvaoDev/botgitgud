from __future__ import annotations

import json
import threading
from pathlib import Path

from botgitgud.domain.spells import SpellCatalog, SpellInfo


def test_concurrent_learn_then_flush_produces_valid_file_with_all_ids(tmp_path: Path) -> None:
    path = tmp_path / "spells.json"
    catalog = SpellCatalog(path, blizzard=None)

    n_threads = 8
    barrier = threading.Barrier(n_threads)

    def worker(i: int) -> None:
        barrier.wait()  # maximize actual concurrent overlap
        catalog.learn(1000 + i, f"Spell {i}", "wcl")

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    catalog.flush()

    assert path.exists()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert len(data) == n_threads
    for i in range(n_threads):
        entry = data[str(1000 + i)]
        assert entry["name"] == f"Spell {i}"
        assert entry["source"] == "wcl"
        assert "category" not in entry


def test_corrupted_json_is_quarantined_and_catalog_starts_empty(tmp_path: Path) -> None:
    path = tmp_path / "spells.json"
    path.write_text("{not valid json", encoding="utf-8")

    catalog = SpellCatalog(path, blizzard=None)

    assert not path.exists()
    quarantine_files = list(tmp_path.glob("spells.corrupt.*.json"))
    assert len(quarantine_files) == 1
    assert quarantine_files[0].read_text(encoding="utf-8") == "{not valid json"

    # Catalog is usable and empty — falls back to the unknown-spell sentinel.
    info = catalog.get(999)
    assert info.spell_id == 999
    assert "999" in info.name
    assert info.source == "unknown"


def test_old_format_with_category_field_loads_and_drops_category(tmp_path: Path) -> None:
    path = tmp_path / "spells.json"
    path.write_text(
        json.dumps(
            {
                "12345": {"name": "Old Spell", "category": "trackable", "source": "wcl"},
                "67890": {
                    "name": "Blacklisted Spell",
                    "category": "non-trackable",
                    "source": "blizzard",
                },
            }
        ),
        encoding="utf-8",
    )

    catalog = SpellCatalog(path, blizzard=None)

    info_a = catalog.get(12345)
    assert info_a.name == "Old Spell"
    assert info_a.source == "wcl"
    assert not hasattr(info_a, "category")

    info_b = catalog.get(67890)
    assert info_b.name == "Blacklisted Spell"
    assert info_b.source == "blizzard"


def test_oldest_format_plain_string_value_loads(tmp_path: Path) -> None:
    path = tmp_path / "spells.json"
    path.write_text(json.dumps({"555": "Ancient Spell"}), encoding="utf-8")

    catalog = SpellCatalog(path, blizzard=None)

    info = catalog.get(555)
    assert info == SpellInfo(spell_id=555, name="Ancient Spell", source="unknown")


def test_get_falls_back_to_blizzard_when_not_cached(tmp_path: Path) -> None:
    class _FakeBlizzard:
        def __init__(self) -> None:
            self.calls: list[int] = []

        def get_spell_name(self, spell_id: int) -> str | None:
            self.calls.append(spell_id)
            return "Fireball" if spell_id == 42 else None

    fake = _FakeBlizzard()
    catalog = SpellCatalog(tmp_path / "spells.json", blizzard=fake)  # type: ignore[arg-type]

    info = catalog.get(42)
    assert info == SpellInfo(spell_id=42, name="Fireball", source="blizzard")
    assert fake.calls == [42]

    # Second call should hit the in-memory cache, not Blizzard again.
    catalog.get(42)
    assert fake.calls == [42]


def test_get_unknown_spell_without_blizzard_client_returns_fallback(tmp_path: Path) -> None:
    catalog = SpellCatalog(tmp_path / "spells.json", blizzard=None)
    info = catalog.get(777)
    assert info.spell_id == 777
    assert "777" in info.name
    assert info.source == "unknown"


def test_learn_ignores_fallback_placeholder_names(tmp_path: Path) -> None:
    catalog = SpellCatalog(tmp_path / "spells.json", blizzard=None)
    catalog.learn(1, "Spell #1", "unknown")
    catalog.flush()
    data = json.loads((tmp_path / "spells.json").read_text(encoding="utf-8"))
    assert data == {}


def test_flush_is_atomic_no_tmp_file_left_behind(tmp_path: Path) -> None:
    path = tmp_path / "spells.json"
    catalog = SpellCatalog(path, blizzard=None)
    catalog.learn(1, "Fireball", "wcl")
    catalog.flush()

    assert path.exists()
    assert not (tmp_path / "spells.json.tmp").exists()


def test_no_category_field_anywhere_in_src() -> None:
    """Direct check for the T0.4 acceptance criterion:
    `grep -r '"category"' src/` deve retornar vazio.
    """
    src_root = Path(__file__).resolve().parents[2] / "src"
    offenders = [p for p in src_root.rglob("*.py") if '"category"' in p.read_text(encoding="utf-8")]
    assert offenders == []
