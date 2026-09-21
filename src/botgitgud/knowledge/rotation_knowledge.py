"""Versioned, prose-free rotation facts. No player matching or finding generation (M28)."""

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal, Self
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, model_validator

from botgitgud.knowledge.spec_slugs import BRANCH_LABELS, SPEC_SLUGS, Branch

SCHEMA_VERSION = 1
PARSER_VERSION = "m33.1"
SpellId = Annotated[int, Field(gt=0)]


class Origin(StrEnum):
    SOURCE_FACT = "SOURCE_FACT"
    BOT_INFERENCE = "BOT_INFERENCE"


class Observability(StrEnum):
    OBSERVABLE = "OBSERVABLE"
    NOT_OBSERVABLE = "NOT_OBSERVABLE"


class Section(StrEnum):
    SINGLE_TARGET = "SINGLE_TARGET"
    AOE = "AOE"
    OPENER = "OPENER"
    COOLDOWN = "COOLDOWN"
    PRE_COMBAT = "PRE_COMBAT"


class ConditionType(StrEnum):
    NONE = "NONE"
    WITH_STATE = "WITH_STATE"
    WITHOUT_STATE = "WITHOUT_STATE"
    AT_TARGET_COUNT = "AT_TARGET_COUNT"
    BEFORE_SPELL = "BEFORE_SPELL"
    UNRECOGNIZED = "UNRECOGNIZED"


class KnowledgeState(StrEnum):
    CURRENT = "CURRENT"
    STALE_UNVERIFIED = "STALE_UNVERIFIED"
    UNAVAILABLE = "UNAVAILABLE"


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class Condition(FrozenModel):
    kind: ConditionType = ConditionType.NONE
    subject_spell_ids: tuple[SpellId, ...] = ()
    negated: bool = False
    comparator: Literal["EQ", "GE", "GT", "LE", "LT"] | None = None
    threshold: Annotated[int, Field(ge=0)] | None = None
    unit: Literal["TARGETS"] | None = None

    @model_validator(mode="after")
    def validate_condition(self) -> Self:
        state_or_spell = self.kind in {
            ConditionType.WITH_STATE,
            ConditionType.WITHOUT_STATE,
            ConditionType.BEFORE_SPELL,
        }
        if state_or_spell != bool(self.subject_spell_ids):
            raise ValueError("Condition subjects must match condition type")
        if self.negated != (self.kind == ConditionType.WITHOUT_STATE):
            raise ValueError("Negation must be preserved")
        numeric = (self.comparator, self.threshold, self.unit)
        if self.kind == ConditionType.AT_TARGET_COUNT:
            if any(value is None for value in numeric):
                raise ValueError("Target condition requires comparator, threshold and unit")
        elif any(value is not None for value in numeric):
            raise ValueError("Unexpected numeric condition")
        return self


def classify_observability(section: Section, condition: Condition) -> Observability:
    if condition.kind in {
        ConditionType.WITH_STATE,
        ConditionType.WITHOUT_STATE,
        ConditionType.BEFORE_SPELL,
    }:
        return Observability.OBSERVABLE
    if condition.kind == ConditionType.NONE and section == Section.OPENER:
        return Observability.OBSERVABLE
    # Priority alternatives require per-GCD readiness; target/resource thresholds
    # cannot be recovered from per-ability averages or aggregate resource waste.
    return Observability.NOT_OBSERVABLE


class RotationRule(FrozenModel):
    section: Section
    branch: Branch
    ordinal: Annotated[int, Field(gt=0)]
    action_spell_ids: Annotated[tuple[SpellId, ...], Field(min_length=1)]
    alternative: bool
    condition: Condition
    origin: Origin
    observability: Observability

    @model_validator(mode="after")
    def validate_rule(self) -> Self:
        if self.alternative and len(self.action_spell_ids) < 2:
            raise ValueError("An alternative needs at least two actions")
        if self.observability != classify_observability(self.section, self.condition):
            raise ValueError("Observability does not match available evidence")
        return self

    @property
    def comparison_eligible(self) -> bool:
        """Knowledge-side gate only. A named branch still needs M28 build proof."""
        return (
            self.branch != Branch.UNRESOLVED
            and self.condition.kind != ConditionType.UNRECOGNIZED
            and self.observability == Observability.OBSERVABLE
        )


class RotationKnowledge(FrozenModel):
    class_name: str
    spec_name: str
    source_provider: Literal["WOWHEAD"] = "WOWHEAD"
    source_url: str
    source_title: Annotated[str, Field(min_length=1, max_length=500)]
    source_author: Annotated[str, Field(min_length=1, max_length=300)]
    source_modified_at: datetime
    retrieved_at: datetime
    artifact_created_at: datetime
    source_fingerprint: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    schema_version: Literal[1] = SCHEMA_VERSION
    parser_version: Literal["m33.1"] = PARSER_VERSION
    ingestion_method: Literal["HTML_ORDERED_LIST_JSON_LD"] = "HTML_ORDERED_LIST_JSON_LD"
    items_total: Annotated[int, Field(gt=0)]
    items_recognized: Annotated[int, Field(gt=0)]
    items_unrecognized: Annotated[int, Field(ge=0)]
    rules_condition_unrecognized: Annotated[int, Field(ge=0)]
    rules_branch_unresolved: Annotated[int, Field(ge=0)]
    sections_skipped: Annotated[int, Field(ge=0)]
    rules: Annotated[tuple[RotationRule, ...], Field(min_length=1)]

    @model_validator(mode="after")
    def validate_artifact(self) -> Self:
        key = (self.class_name, self.spec_name)
        slug = SPEC_SLUGS.get(key)
        if slug is None:
            raise ValueError("Unsupported artifact identity")
        url = urlparse(self.source_url)
        path = f"/guide/classes/{slug.class_slug}/{slug.spec_slug}/"
        if (
            url.scheme != "https"
            or url.hostname not in {"www.wowhead.com", "wowhead.com"}
            or url.username
            or url.password
            or not url.path.startswith(path + "rotation")
        ):
            raise ValueError("Unsupported source URL")
        for timestamp in (self.source_modified_at, self.retrieved_at, self.artifact_created_at):
            if timestamp.tzinfo is None or timestamp.utcoffset() is None:
                raise ValueError("Provenance timestamps require a timezone")
        # §3E: a claimed <li> with no spell link is counted (items_unrecognized),
        # never fatal on its own — it emits no rule, so the recognized count is
        # what must be backed by rules, not the raw total including prose.
        if self.items_total != self.items_recognized + self.items_unrecognized:
            raise ValueError("Item accounting mismatch")
        if len(self.rules) < self.items_recognized:
            raise ValueError("Partial extraction")
        if self.rules_condition_unrecognized != sum(
            rule.condition.kind == ConditionType.UNRECOGNIZED for rule in self.rules
        ):
            raise ValueError("Invalid condition audit count")
        if self.rules_branch_unresolved != sum(
            rule.branch == Branch.UNRESOLVED for rule in self.rules
        ):
            raise ValueError("Invalid branch audit count")
        allowed = {Branch.ALL, Branch.UNRESOLVED, *BRANCH_LABELS.get(key, {}).values()}
        if any(rule.branch not in allowed for rule in self.rules):
            raise ValueError("Branch not curated for this spec")
        return self
