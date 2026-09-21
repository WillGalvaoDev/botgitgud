from __future__ import annotations

from pathlib import Path

from botgitgud.domain.spells import SpellCatalog, SpellInfo


def test_unknown_spell_without_blizzard_is_structurally_unknown(tmp_path: Path) -> None:
    catalog = SpellCatalog(tmp_path / "spells.json", blizzard=None)

    assert catalog.get(12345) == SpellInfo(
        spell_id=12345,
        name="Nome de spell não resolvido (ID: 12345)",
        source="unknown",
    )
