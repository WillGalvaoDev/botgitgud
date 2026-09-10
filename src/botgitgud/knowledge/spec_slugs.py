"""Curated Wowhead identities; normalization cannot reconstruct hyphens."""

from dataclasses import dataclass
from enum import StrEnum

from botgitgud.domain.specs import _SUPPORTED, SpecId, _normalize


class Branch(StrEnum):
    ALL = "ALL"
    UNRESOLVED = "UNRESOLVED"
    DIABOLIST = "DIABOLIST"
    SOUL_HARVESTER = "SOUL_HARVESTER"


@dataclass(frozen=True)
class SpecSlug:
    class_name: str
    spec_name: str
    class_slug: str
    spec_slug: str

    @property
    def key(self) -> tuple[str, str]:
        return spec_key(SpecId(self.class_name, self.spec_name))

    @property
    def url(self) -> str:
        return f"https://www.wowhead.com/guide/classes/{self.class_slug}/{self.spec_slug}/rotation"


def spec_key(spec: SpecId) -> tuple[str, str]:
    return _normalize(spec.class_name), _normalize(spec.spec_name)


SPEC_SLUGS = {
    entry.key: entry
    for entry in (
        SpecSlug("Death Knight", "Frost", "death-knight", "frost"),
        SpecSlug("Death Knight", "Unholy", "death-knight", "unholy"),
        SpecSlug("Demon Hunter", "Havoc", "demon-hunter", "havoc"),
        SpecSlug("Demon Hunter", "Devourer", "demon-hunter", "devourer"),
        SpecSlug("Druid", "Balance", "druid", "balance"),
        SpecSlug("Druid", "Feral", "druid", "feral"),
        SpecSlug("Evoker", "Devastation", "evoker", "devastation"),
        SpecSlug("Hunter", "Beast Mastery", "hunter", "beast-mastery"),
        SpecSlug("Hunter", "Marksmanship", "hunter", "marksmanship"),
        SpecSlug("Hunter", "Survival", "hunter", "survival"),
        SpecSlug("Mage", "Arcane", "mage", "arcane"),
        SpecSlug("Mage", "Fire", "mage", "fire"),
        SpecSlug("Mage", "Frost", "mage", "frost"),
        SpecSlug("Monk", "Windwalker", "monk", "windwalker"),
        SpecSlug("Paladin", "Retribution", "paladin", "retribution"),
        SpecSlug("Priest", "Shadow", "priest", "shadow"),
        SpecSlug("Rogue", "Assassination", "rogue", "assassination"),
        SpecSlug("Rogue", "Outlaw", "rogue", "outlaw"),
        SpecSlug("Rogue", "Subtlety", "rogue", "subtlety"),
        SpecSlug("Shaman", "Elemental", "shaman", "elemental"),
        SpecSlug("Shaman", "Enhancement", "shaman", "enhancement"),
        SpecSlug("Warlock", "Affliction", "warlock", "affliction"),
        SpecSlug("Warlock", "Demonology", "warlock", "demonology"),
        SpecSlug("Warlock", "Destruction", "warlock", "destruction"),
        SpecSlug("Warrior", "Arms", "warrior", "arms"),
        SpecSlug("Warrior", "Fury", "warrior", "fury"),
    )
}

BRANCH_LABELS = {
    ("warlock", "demonology"): {
        "diabolist": Branch.DIABOLIST,
        "soul harvester": Branch.SOUL_HARVESTER,
    }
}


def validate_inventory() -> None:
    if SPEC_SLUGS.keys() != _SUPPORTED:
        raise ValueError("Wowhead slug map must cover exactly the supported DPS inventory")
