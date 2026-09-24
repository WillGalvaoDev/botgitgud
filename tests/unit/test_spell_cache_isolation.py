"""D-35 - o cache de spells de producao nunca pode tocar o arquivo versionado.

Incidente: rodar o bot de verdade reescrevia o `spells.json` rastreado (100 ->
362 entradas, campo `category` removido pela migracao da T0.4), sujando a
working tree e quebrando os golden tests legados, que dependem do formato
antigo. Ver docs/operations.md.
"""

from __future__ import annotations

import json
from pathlib import Path

from botgitgud.domain.spells import (
    CATALOG_FILENAME,
    SpellCatalog,
    open_runtime_catalog,
    runtime_catalog_path,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
VERSIONED_SEED = REPO_ROOT / CATALOG_FILENAME


def _seed(path: Path, entries: dict[str, dict[str, str]]) -> Path:
    path.write_text(json.dumps(entries), encoding="utf-8")
    return path


# -- caminho de runtime -----------------------------------------------------------


def test_runtime_path_lives_under_the_data_dir(tmp_path: Path) -> None:
    assert runtime_catalog_path(tmp_path) == tmp_path / "spells.json"


def test_runtime_path_is_never_the_versioned_seed(tmp_path: Path) -> None:
    assert runtime_catalog_path(tmp_path) != VERSIONED_SEED


def test_first_boot_seeds_the_runtime_cache_from_the_versioned_file(tmp_path: Path) -> None:
    seed = _seed(tmp_path / "seed.json", {"111": {"name": "Fireball", "source": "wcl"}})
    data_dir = tmp_path / "data"

    catalog = open_runtime_catalog(data_dir, blizzard=None, seed_path=seed)

    assert runtime_catalog_path(data_dir).is_file()
    assert catalog.get(111).name == "Fireball"


def test_first_boot_without_a_seed_starts_empty_instead_of_failing(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    catalog = open_runtime_catalog(data_dir, blizzard=None, seed_path=tmp_path / "nao-existe.json")
    info = catalog.get(999)
    assert "999" in info.name
    assert info.source == "unknown"


def test_existing_runtime_cache_takes_precedence_over_the_seed(tmp_path: Path) -> None:
    seed = _seed(tmp_path / "seed.json", {"111": {"name": "DoSeed", "source": "wcl"}})
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    _seed(runtime_catalog_path(data_dir), {"111": {"name": "DoCache", "source": "wcl"}})

    catalog = open_runtime_catalog(data_dir, blizzard=None, seed_path=seed)

    assert catalog.get(111).name == "DoCache"


# -- o seed versionado e somente leitura ------------------------------------------


def test_learn_and_flush_only_ever_touch_the_runtime_cache(tmp_path: Path) -> None:
    seed = _seed(tmp_path / "seed.json", {"111": {"name": "Fireball", "source": "wcl"}})
    seed_before = seed.read_bytes()
    data_dir = tmp_path / "data"

    catalog = open_runtime_catalog(data_dir, blizzard=None, seed_path=seed)
    catalog.learn(222, "Frostbolt", "wcl")
    catalog.flush()

    assert seed.read_bytes() == seed_before  # seed intocado
    written = json.loads(runtime_catalog_path(data_dir).read_text(encoding="utf-8"))
    assert written["222"]["name"] == "Frostbolt"


def test_production_flush_never_writes_the_repository_spells_json(tmp_path: Path) -> None:
    """A regressao direta do incidente: o arquivo rastreado do repo nao pode
    mudar por causa de um fluxo de runtime.
    """
    before = VERSIONED_SEED.read_bytes()

    catalog = open_runtime_catalog(tmp_path / "data", blizzard=None, seed_path=VERSIONED_SEED)
    catalog.learn(424242, "Spell Aprendido Em Runtime", "wcl")
    catalog.flush()

    assert VERSIONED_SEED.read_bytes() == before
    assert "424242" not in VERSIONED_SEED.read_text(encoding="utf-8")


def test_restart_reuses_the_runtime_cache_and_keeps_what_was_learned(tmp_path: Path) -> None:
    seed = _seed(tmp_path / "seed.json", {"111": {"name": "Fireball", "source": "wcl"}})
    data_dir = tmp_path / "data"

    first = open_runtime_catalog(data_dir, blizzard=None, seed_path=seed)
    first.learn(222, "Frostbolt", "wcl")
    first.flush()

    second = open_runtime_catalog(data_dir, blizzard=None, seed_path=seed)
    assert second.get(222).name == "Frostbolt"
    assert second.get(111).name == "Fireball"


def test_runtime_cache_can_be_deleted_and_rebuilt(tmp_path: Path) -> None:
    seed = _seed(tmp_path / "seed.json", {"111": {"name": "Fireball", "source": "wcl"}})
    data_dir = tmp_path / "data"

    open_runtime_catalog(data_dir, blizzard=None, seed_path=seed).flush()
    runtime_catalog_path(data_dir).unlink()

    rebuilt = open_runtime_catalog(data_dir, blizzard=None, seed_path=seed)
    assert rebuilt.get(111).name == "Fireball"


def test_corrupt_runtime_cache_follows_the_existing_quarantine_policy(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    runtime_catalog_path(data_dir).write_text("{nao e json", encoding="utf-8")

    catalog = open_runtime_catalog(data_dir, blizzard=None, seed_path=None)

    info = catalog.get(1)
    assert "1" in info.name  # inicia vazio
    assert info.source == "unknown"
    assert list(data_dir.glob("spells.corrupt.*.json"))  # quarentena preservada


# -- a decisao da T0.4 sobre `category` continua valida ----------------------------


def test_modern_catalog_still_discards_category_on_read_and_write(tmp_path: Path) -> None:
    path = tmp_path / "spells.json"
    _seed(path, {"111": {"name": "Fireball", "source": "wcl", "category": "trackable"}})

    catalog = SpellCatalog(path, blizzard=None)
    catalog.flush()

    written = json.loads(path.read_text(encoding="utf-8"))
    assert written["111"] == {"name": "Fireball", "source": "wcl"}
    assert "category" not in written["111"]


def test_production_never_depends_on_category(tmp_path: Path) -> None:
    path = tmp_path / "spells.json"
    _seed(path, {"111": {"name": "Fireball", "source": "wcl"}})
    assert SpellCatalog(path, blizzard=None).get(111).name == "Fireball"
