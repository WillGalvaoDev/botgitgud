"""Explicit administrative ingestion. This module is never imported by local readers."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlparse

import httpx
import structlog

from botgitgud.domain.specs import SpecId
from botgitgud.knowledge.rotation_knowledge import (
    Condition,
    ConditionType,
    Origin,
    RotationKnowledge,
    RotationRule,
    Section,
    classify_observability,
)
from botgitgud.knowledge.spec_slugs import (
    BRANCH_LABELS,
    SPEC_SLUGS,
    Branch,
    SpecSlug,
    spec_key,
    validate_inventory,
)
from botgitgud.knowledge.store import KnowledgeStore

log = structlog.get_logger(__name__)

_SPELL = re.compile(r"(?:/|[?&])spell=(\d+)(?:\D|$)")
_TOKEN = re.compile(r"\{SPELL:(\d+)\}")
_MARKER = re.compile(
    r"\b(?:when|if|while|with|without|unless|above|below|during|before|after|at|only|not)\b",
    re.IGNORECASE,
)


class PartialExtraction(ValueError):
    """Structural incompleteness must preserve the previous artifact."""


@dataclass
class _Node:
    tag: str
    attrs: dict[str, str]
    children: list[_Node | str] = field(default_factory=list)

    def text(self, *, spell_tokens: bool = False) -> str:
        match = _SPELL.search(self.attrs.get("href", "")) if self.tag == "a" else None
        if spell_tokens and match:
            return "{SPELL:" + match[1] + "}"
        return "".join(
            child.text(spell_tokens=spell_tokens) if isinstance(child, _Node) else child
            for child in self.children
        )

    def walk(self) -> Iterator[_Node]:
        yield self
        for child in self.children:
            if isinstance(child, _Node):
                yield from child.walk()


class _Document(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = _Node("document", {})
        self.stack = [self.root]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        # HTML permits omitted </li>; close the previous item at the same list level.
        if tag == "li" and self.stack[-1].tag == "li":
            self.stack.pop()
        node = _Node(tag, {key: value or "" for key, value in attrs})
        self.stack[-1].children.append(node)
        if tag not in {
            "area",
            "base",
            "br",
            "col",
            "embed",
            "hr",
            "img",
            "input",
            "link",
            "meta",
            "param",
            "source",
            "track",
            "wbr",
        }:
            self.stack.append(node)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                return

    def handle_data(self, data: str) -> None:
        self.stack[-1].children.append(data)


def _events(node: _Node) -> Iterator[_Node | str]:
    if node.tag in {"script", "style", "nav", "footer", "header"}:
        return
    if node.tag in {"h2", "h3", "ol"}:
        yield node
        return
    for child in node.children:
        if isinstance(child, str):
            yield child
        else:
            yield from _events(child)


def _json_objects(value: Any) -> Iterator[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _json_objects(child)
    elif isinstance(value, list):
        for child in value:
            yield from _json_objects(child)


def _metadata(root: _Node) -> tuple[str, str, datetime]:
    candidates: list[tuple[str, str, datetime]] = []
    for node in root.walk():
        if node.tag != "script" or node.attrs.get("type", "").lower() != "application/ld+json":
            continue
        for obj in _json_objects(json.loads(node.text())):
            if "dateModified" not in obj:
                continue
            title = obj.get("headline") or obj.get("name")
            author = obj.get("author")
            if isinstance(author, list):
                author = ", ".join(
                    item.get("name", "") if isinstance(item, dict) else str(item) for item in author
                )
            elif isinstance(author, dict):
                author = author.get("name")
            if isinstance(title, str) and isinstance(author, str) and title and author:
                candidates.append((title, author, datetime.fromisoformat(obj["dateModified"])))
    if len(set(candidates)) != 1:
        raise PartialExtraction("Missing or ambiguous JSON-LD provenance")
    return candidates[0]


def _section(heading: str) -> Section | None:
    if "single target" in heading or "single-target" in heading:
        return Section.SINGLE_TARGET
    if re.search(r"\b(?:aoe|multi-target|multi target)\b", heading):
        return Section.AOE
    if "opener" in heading:
        return Section.OPENER
    if "major cooldowns" in heading:
        return Section.COOLDOWN
    if "pre-combat" in heading or "pre combat" in heading:
        return Section.PRE_COMBAT
    return None


@dataclass
class _ListContext:
    node: _Node
    section: Section
    parent: int
    priority: int | None
    labels: str


@dataclass
class _ListResult:
    contexts: list[tuple[_ListContext, bool]]
    sections_skipped: int


def _lists(root: _Node) -> _ListResult:
    active = False
    section: Section | None = None
    # §3D: a heading the vocabulary does not enumerate is out of the declared
    # scope, not a failure. Its subtree (any text/<ol> up to the next heading)
    # is ignored rather than raising, and the gap is counted, never silent.
    skipping = False
    sections_skipped = 0
    parent = 0
    priority: int | None = None
    priorities: dict[int, int] = {}
    contexts: list[_ListContext] = []
    pending_text = ""
    priority_labels = ""
    # Cooldown headings can identify an actual linked spell by its source label.
    spell_names = {
        " ".join(node.text().lower().split())
        for node in root.walk()
        if node.tag == "a" and _SPELL.search(node.attrs.get("href", ""))
    }
    for event in _events(root):
        if isinstance(event, str):
            if active and not skipping:
                pending_text += event
            continue
        if event.tag in {"h2", "h3"}:
            heading = " ".join(event.text().lower().split())
            if not active:
                if event.tag == "h2" and "rotation" in heading:
                    active = True
                continue
            if heading == "priority":
                if section is None:
                    raise PartialExtraction("Priority has no section")
                skipping = False
                priorities[parent] = priorities.get(parent, 0) + 1
                priority = priorities[parent]
                priority_labels = pending_text
            elif heading in {"sample timeline", "opener sequence"} and section == Section.OPENER:
                skipping = False
                priority_labels = ""
            elif new_section := _section(heading):
                skipping = False
                section = new_section
                parent += 1
                priority = None
                priority_labels = ""
            elif section == Section.COOLDOWN and event.tag == "h3" and heading in spell_names:
                skipping = False
                parent += 1
                priority = None
                priority_labels = ""
            elif event.tag == "h2" and any(
                word in heading
                for word in ("gear", "consumables", "stat priority", "related guides")
            ):
                # Explicitly outside M33's rotation region.
                active = False
                section = None
                skipping = False
            else:
                # §3D: unrecognized heading — skip its subtree, count it, do not reject.
                skipping = True
                sections_skipped += 1
            pending_text = ""
        elif active:
            if skipping:
                # An <ol> under an unrecognized heading is out of scope, not
                # an "unclaimed list" — that failure is reserved for lists
                # inside the region we declare we understand.
                continue
            if section is None:
                raise PartialExtraction("Unclaimed ordered list")
            contexts.append(
                _ListContext(event, section, parent, priority, priority_labels + pending_text)
            )
            pending_text = ""
            priority_labels = ""
    return _ListResult(
        contexts=[(ctx, priorities.get(ctx.parent, 0) >= 2) for ctx in contexts],
        sections_skipped=sections_skipped,
    )


def _ids(text: str) -> tuple[int, ...]:
    return tuple(dict.fromkeys(int(match[1]) for match in _TOKEN.finditer(text)))


def _condition(text: str) -> tuple[Condition, str]:
    """Only the closed grammar is recognized. Unmatched markers remain ineligible."""
    token = r"\{SPELL:\d+\}"
    actions = rf"{token}(?:\s*(?:/|,|and)\s*{token})*"
    # Match complete clauses, not substrings: "With A and B cast C" cannot
    # silently turn B into an action, nor can "before A or B" lose its branch.
    state_clause = rf"(?:with|without)\s+{token}\s*,?\s*(?:cast|use)\s+{actions}"
    before_clause = rf"(?:cast|use)\s+{actions}\s+before\s+(?:casting\s+)?{token}"
    target_clause = rf"at\s+\d+\+?\s+targets?\s*,?\s*(?:cast|use)\s+{actions}"
    target_suffix = rf"(?:cast|use)\s+{actions}\s+at\s+\d+\+?\s+targets?"
    grammar = rf"\s*(?:{state_clause}|{before_clause}|{target_clause}|{target_suffix})[.\s]*"
    if _MARKER.search(text) and not re.fullmatch(grammar, text, re.IGNORECASE):
        return Condition(kind=ConditionType.UNRECOGNIZED), text
    state = re.search(r"\b(without|with)\s+(\{SPELL:\d+\})", text, re.IGNORECASE)
    before = re.search(r"\bbefore\s+(?:casting\s+)?(\{SPELL:\d+\})", text, re.IGNORECASE)
    targets = re.search(r"\bat\s+(\d+)(\+)?\s+targets?\b", text, re.IGNORECASE)
    match = state or before or targets
    if match is None:
        kind = ConditionType.UNRECOGNIZED if _MARKER.search(text) else ConditionType.NONE
        return Condition(kind=kind), text
    remaining = text[: match.start()] + text[match.end() :]
    if _MARKER.search(remaining):
        # E.g. "with X only when ..." must not turn into just WITH_STATE.
        return Condition(kind=ConditionType.UNRECOGNIZED), text
    if state:
        absent = state[1].lower() == "without"
        return Condition(
            kind=ConditionType.WITHOUT_STATE if absent else ConditionType.WITH_STATE,
            subject_spell_ids=_ids(state[2]),
            negated=absent,
        ), remaining
    if before:
        return Condition(
            kind=ConditionType.BEFORE_SPELL, subject_spell_ids=_ids(before[1])
        ), remaining
    assert targets is not None
    return Condition(
        kind=ConditionType.AT_TARGET_COUNT,
        comparator="GE" if targets[2] else "EQ",
        threshold=int(targets[1]),
        unit="TARGETS",
    ), remaining


def _item_rules(text: str, ctx: _ListContext, branch: Branch, ordinal: int) -> list[RotationRule]:
    # §3E: an item with no spell link at all is the caller's concern (counted
    # as items_unrecognized, never reaching here) — by the time we're called,
    # at least one spell id is guaranteed to exist in `text`.
    # The measured paired form inherits the subject of WITH into WITHOUT.
    paired = re.fullmatch(
        r"\s*With\s+(\{SPELL:\d+\})\s+cast\s+(\{SPELL:\d+\})\s*,\s*"
        r"without\s+cast\s+(\{SPELL:\d+\})[.\s]*",
        text,
        re.IGNORECASE,
    )
    clauses = (
        [f"With {paired[1]} cast {paired[2]}", f"Without {paired[1]} cast {paired[3]}"]
        if paired
        else [text]
    )
    rules = []
    for clause in clauses:
        condition, actions = _condition(clause)
        action_ids = _ids(actions)
        if not action_ids:
            # Spell links existed but their action/subject roles are not resolved.
            condition = Condition(kind=ConditionType.UNRECOGNIZED)
            actions = clause
            action_ids = _ids(clause)
        alternative = bool(re.search(r"\{SPELL:\d+\}\s*/\s*\{SPELL:\d+\}", actions))
        rules.append(
            RotationRule(
                section=ctx.section,
                branch=branch,
                ordinal=ordinal,
                action_spell_ids=action_ids,
                alternative=alternative,
                condition=condition,
                origin=Origin.SOURCE_FACT,
                observability=classify_observability(ctx.section, condition),
            )
        )
    return rules


def parse_guide(
    html: str, slug: SpecSlug, final_url: str, retrieved_at: datetime
) -> RotationKnowledge:
    document = _Document()
    document.feed(html)
    document.close()
    title, author, modified = _metadata(document.root)
    rules: list[RotationRule] = []
    total = 0
    recognized = 0
    items_unrecognized = 0
    lists = _lists(document.root)
    for ctx, branched in lists.contexts:
        branch = Branch.ALL
        if branched:
            labels = " ".join(ctx.labels.lower().split())
            matches = {
                value
                for label, value in BRANCH_LABELS.get(slug.key, {}).items()
                if re.search(r"\b" + re.escape(label) + r"\b", labels)
            }
            branch = next(iter(matches)) if len(matches) == 1 else Branch.UNRESOLVED
        items = [node for node in ctx.node.walk() if node.tag == "li"]
        if not items or any(node.tag == "ol" for node in list(ctx.node.walk())[1:]):
            raise PartialExtraction("Empty or nested ordered list")
        for ordinal, item in enumerate(items, start=1):
            total += 1
            text = item.text(spell_tokens=True)
            if not _ids(text):
                # §3E: real opener rules are written as prose with no spell
                # link ("Precast Demonbolt...", "Summon Demonic Tyrant"). This
                # is source prose, not a parser failure — count it, emit no
                # rule, and keep going. Resolving names against the spell
                # catalogue is explicitly out of scope (§3E, §18.5).
                items_unrecognized += 1
                continue
            recognized += 1
            rules.extend(_item_rules(text, ctx, branch, ordinal))
    # §3F: a recognized section that ends up with zero claimed lists (or zero
    # rules) is no longer fatal on its own. Three rounds against the real page
    # (§3D unknown heading, §3E spell-less item, and this one) each broke on
    # a completeness assumption the source doesn't honor — COOLDOWN and
    # PRE_COMBAT are narrative sections with no <ol> at all. The floor that
    # remains is only what actually proves the parser understood the page.
    if not rules:
        raise PartialExtraction("Zero rotation rules")
    if not any(rule.section == Section.SINGLE_TARGET for rule in rules):
        # Single Target is the core of a rotation guide; absent or empty rejects
        # even when other sections produced rules.
        raise PartialExtraction("Single target section absent or empty")
    return RotationKnowledge(
        class_name=slug.key[0],
        spec_name=slug.key[1],
        source_url=final_url,
        source_title=title,
        source_author=author,
        source_modified_at=modified,
        retrieved_at=retrieved_at,
        artifact_created_at=datetime.now(UTC),
        source_fingerprint=hashlib.sha256(html.encode("utf-8")).hexdigest(),
        items_total=total,
        items_recognized=recognized,
        items_unrecognized=items_unrecognized,
        rules_condition_unrecognized=sum(
            rule.condition.kind == ConditionType.UNRECOGNIZED for rule in rules
        ),
        rules_branch_unresolved=sum(rule.branch == Branch.UNRESOLVED for rule in rules),
        sections_skipped=lists.sections_skipped,
        rules=tuple(rules),
    )


class UpdateStatus(StrEnum):
    UPDATED = "UPDATED"
    UNCHANGED = "UNCHANGED"
    FAILED = "FAILED"
    UNAVAILABLE = "UNAVAILABLE"
    UNSUPPORTED = "UNSUPPORTED"


@dataclass(frozen=True)
class UpdateResult:
    spec: SpecId
    status: UpdateStatus
    previous_preserved: bool = False


class WowheadSource:
    def __init__(self, client: httpx.Client) -> None:
        self.client = client

    def fetch(self, slug: SpecSlug) -> httpx.Response:
        # Bound redirects and restrict every hop to the authorized source.
        url = slug.url
        for _ in range(6):
            parsed = urlparse(url)
            if parsed.scheme != "https" or parsed.hostname not in {
                "www.wowhead.com",
                "wowhead.com",
            }:
                raise ValueError("Unsupported redirect source")
            response = self.client.get(url, follow_redirects=False, timeout=20.0)
            if not response.is_redirect:
                return response
            location = response.headers.get("location")
            if not location:
                raise ValueError("Redirect without location")
            url = str(response.url.join(location))
        raise ValueError("Too many redirects")

    def update_one(self, store: KnowledgeStore, spec: SpecId) -> UpdateResult:
        key = spec_key(spec)
        slug = SPEC_SLUGS.get(key)
        if slug is None:
            return UpdateResult(spec, UpdateStatus.UNSUPPORTED)
        previous = store.read(spec).artifact
        store.mark_unverified(spec)
        preserved = previous is not None
        try:
            response = self.fetch(slug)
            if response.status_code == 404:
                return UpdateResult(spec, UpdateStatus.UNAVAILABLE, preserved)
            response.raise_for_status()
            if "text/html" not in response.headers.get("content-type", "").lower():
                raise ValueError("Not an HTML guide")
            artifact = parse_guide(response.text, slug, str(response.url), datetime.now(UTC))
            if previous is not None and (
                previous.source_modified_at == artifact.source_modified_at
                and previous.source_fingerprint == artifact.source_fingerprint
            ):
                store.mark_verified(previous)
                return UpdateResult(spec, UpdateStatus.UNCHANGED, True)
            store.write(artifact)
            return UpdateResult(spec, UpdateStatus.UPDATED)
        except Exception as error:
            # Administrative per-spec isolation: even an unexpected parser failure
            # must not undo or prevent the other specs' independent updates.
            log.warning(
                "rotation_knowledge.update_failed",
                class_name=slug.class_name,
                spec_name=slug.spec_name,
                error_type=type(error).__name__,
            )
            return UpdateResult(spec, UpdateStatus.FAILED, preserved)

    def update_all(self, store: KnowledgeStore) -> tuple[UpdateResult, ...]:
        validate_inventory()
        return tuple(
            self.update_one(store, SpecId(slug.class_name, slug.spec_name))
            for slug in SPEC_SLUGS.values()
        )


def update_references(store: KnowledgeStore) -> tuple[UpdateResult, ...]:
    with httpx.Client(headers={"User-Agent": "BotGitGud prototype rotation reference"}) as client:
        return WowheadSource(client).update_all(store)


def format_update_results(results: Sequence[UpdateResult]) -> str:
    updated = sum(result.status == UpdateStatus.UPDATED for result in results)
    unchanged = sum(result.status == UpdateStatus.UNCHANGED for result in results)
    failures = [
        r for r in results if r.status not in {UpdateStatus.UPDATED, UpdateStatus.UNCHANGED}
    ]
    lines = [
        "Referencias de rotacao atualizadas.",
        f"Atualizadas: {updated} · Inalteradas: {unchanged} · Falharam: {len(failures)}",
    ]
    if failures:
        lines.extend(("", "Falhou:"))
        for result in failures:
            reference = "anterior preservada" if result.previous_preserved else "indisponivel"
            lines.append(
                f"{result.spec.class_name} / {result.spec.spec_name} — referencia {reference}."
            )
    # The full supported inventory can fail; keep the single Discord message <2000 chars.
    omitted = 0
    while len("\n".join(lines)) > 1900:
        lines.pop()
        omitted += 1
    if omitted:
        lines.append(f"Outras falhas: {omitted}.")
    return "\n".join(lines)
