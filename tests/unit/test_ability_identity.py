from __future__ import annotations

import itertools
import random
from pathlib import Path

import pytest

from botgitgud.domain.ability_identity import AbilityIdentity, IdentitySource
from botgitgud.domain.spells import SpellCatalog


def test_resolved_and_unresolved_identity_contract() -> None:
    resolved = AbilityIdentity(1, "Known", IdentitySource.WCL_TABLE, "resolved")
    unresolved = AbilityIdentity(2, "", IdentitySource.UNRESOLVED, "unresolved")
    assert resolved.resolved_name == "Known"
    assert unresolved.resolution_status == "unresolved"


@pytest.mark.parametrize(
    ("name", "source", "status"),
    [
        ("", IdentitySource.WCL_TABLE, "resolved"),
        ("Known", IdentitySource.UNRESOLVED, "resolved"),
        ("Known", IdentitySource.WCL_TABLE, "unresolved"),
        ("", IdentitySource.UNRESOLVED, "resolved"),
        ("", IdentitySource.WCL_TABLE, "unresolved"),
        ("Known", IdentitySource.UNRESOLVED, "unresolved"),
    ],
)
def test_identity_rejects_every_inconsistent_combination(
    name: str, source: IdentitySource, status: str
) -> None:
    with pytest.raises(ValueError):
        AbilityIdentity(1, name, source, status)  # type: ignore[arg-type]


def test_identity_rejects_unknown_resolution_status() -> None:
    with pytest.raises(ValueError):
        AbilityIdentity(1, "Known", IdentitySource.WCL_TABLE, "invalid")  # type: ignore[arg-type]


SOURCES = (
    IdentitySource.LEGACY_CATALOG,
    IdentitySource.BLIZZARD_GAME_DATA,
    IdentitySource.WCL_GAME_DATA,
    IdentitySource.WCL_TABLE,
    IdentitySource.WCL_REPORT_MASTER_DATA,
    IdentitySource.CURATED,
)


@pytest.mark.parametrize(("first", "second"), itertools.product(SOURCES, repeat=2))
def test_precedence_is_strict_and_total(
    tmp_path: Path, first: IdentitySource, second: IdentitySource
) -> None:
    catalog = SpellCatalog(tmp_path / "spells.json", blizzard=None)
    catalog.learn(1, first.value, first)
    catalog.learn(1, second.value, second)
    expected = max((first, second), key=SOURCES.index)
    identity = catalog.identity(1)
    assert identity.identity_source is expected
    assert identity.resolved_name == expected.value


def test_source_arrival_permutations_produce_the_same_identity(tmp_path: Path) -> None:
    observations = [(source, source.value) for source in SOURCES]
    expected = AbilityIdentity(1, "curated", IdentitySource.CURATED, "resolved")
    rng = random.Random(16)
    for index in range(30):
        rng.shuffle(observations)
        catalog = SpellCatalog(tmp_path / f"spells-{index}.json", blizzard=None)
        for source, name in observations:
            catalog.learn(1, name, source)
        assert catalog.identity(1) == expected


@pytest.mark.parametrize(
    ("persisted", "expected"),
    [
        ("wcl", IdentitySource.WCL_TABLE),
        ("blizzard", IdentitySource.BLIZZARD_GAME_DATA),
        ("unknown", IdentitySource.LEGACY_CATALOG),
    ],
)
def test_legacy_persisted_sources_map_conservatively(
    tmp_path: Path, persisted: str, expected: IdentitySource
) -> None:
    catalog = SpellCatalog(tmp_path / "spells.json", blizzard=None)
    catalog.learn(1, "Known", persisted)
    assert catalog.identity(1).identity_source is expected


def test_wcl_canonical_id_resolution_never_calls_blizzard(tmp_path: Path) -> None:
    class _FailIfCalled:
        def get_spell_name(self, spell_id: int) -> str | None:
            pytest.fail(f"WCL canonical ID was sent to Blizzard: {spell_id}")

    catalog = SpellCatalog(
        tmp_path / "spells.json",
        blizzard=_FailIfCalled(),  # type: ignore[arg-type]
    )

    assert catalog.identity(434635).resolution_status == "unresolved"
