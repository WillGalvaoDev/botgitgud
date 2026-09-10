"""M33: synthetic-only extraction and local persistence; no real network requests."""

from __future__ import annotations

import ast
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import ValidationError

from botgitgud.domain.specs import _SUPPORTED, SpecId
from botgitgud.knowledge import KnowledgeState, KnowledgeStore, RotationKnowledge, spec_slugs
from botgitgud.knowledge import store as store_module
from botgitgud.knowledge.rotation_knowledge import (
    Condition,
    ConditionType,
    Observability,
    Origin,
    RotationRule,
    Section,
)
from botgitgud.knowledge.spec_slugs import SPEC_SLUGS, Branch, validate_inventory
from botgitgud.knowledge.wowhead_source import (
    PartialExtraction,
    UpdateStatus,
    WowheadSource,
    parse_guide,
)

SPEC = SpecId("Warlock", "Demonology")
SLUG = SPEC_SLUGS[("warlock", "demonology")]
FINAL_URL = SLUG.url + "-cooldowns-pve-dps"
FIXTURE = Path(__file__).parents[1] / "fixtures" / "rotation_guide_synthetic.html"
NOW = datetime(2026, 9, 9, tzinfo=UTC)


def _html() -> str:
    return FIXTURE.read_text(encoding="utf-8")


def _artifact(html: str | None = None) -> RotationKnowledge:
    return parse_guide(_html() if html is None else html, SLUG, FINAL_URL, NOW)


def _client(html: str, status: int = 200) -> httpx.Client:
    return httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                status,
                text=html,
                headers={"content-type": "text/html"},
                request=request,
            )
        )
    )


def test_inventory_is_exact_and_missing_slug_is_configuration_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert len(_SUPPORTED) == len(SPEC_SLUGS) == 26
    assert SPEC_SLUGS.keys() == _SUPPORTED
    validate_inventory()
    assert SPEC_SLUGS[("hunter", "beastmastery")].spec_slug == "beast-mastery"
    assert SPEC_SLUGS[("deathknight", "frost")].class_slug == "death-knight"
    monkeypatch.delitem(spec_slugs.SPEC_SLUGS, ("warlock", "demonology"))
    with pytest.raises(ValueError, match="exactly"):
        validate_inventory()


def test_extraction_preserves_roles_negation_context_and_provenance() -> None:
    artifact = _artifact()
    assert artifact.items_total == artifact.items_recognized == 9
    assert len(artifact.rules) == 10  # paired WITH/WITHOUT expands one item
    present, absent, choice, sequence, targets, unknown, compound, opener, *_ = artifact.rules
    assert present.action_spell_ids == (900002,)
    assert absent.action_spell_ids == (900003,)
    assert present.condition.subject_spell_ids == absent.condition.subject_spell_ids == (900001,)
    assert present.condition.kind == ConditionType.WITH_STATE
    assert absent.condition.kind == ConditionType.WITHOUT_STATE
    assert not present.condition.negated and absent.condition.negated
    assert present.ordinal == absent.ordinal == 1
    assert present.branch == Branch.DIABOLIST
    assert choice.action_spell_ids == (900004, 900005) and choice.alternative
    assert sequence.branch == Branch.SOUL_HARVESTER
    assert sequence.condition.kind == ConditionType.BEFORE_SPELL
    assert sequence.condition.subject_spell_ids == (900007,)
    assert sequence.action_spell_ids == (900006,)
    assert targets.branch == Branch.ALL
    assert targets.condition.threshold == 3 and targets.condition.comparator == "GE"
    assert targets.observability == Observability.NOT_OBSERVABLE
    assert unknown.condition.kind == compound.condition.kind == ConditionType.UNRECOGNIZED
    assert artifact.rules_condition_unrecognized == 2
    assert artifact.rules_branch_unresolved == 0
    assert opener.section == Section.OPENER and opener.comparison_eligible
    assert all(rule.origin == Origin.SOURCE_FACT for rule in artifact.rules)
    assert artifact.source_modified_at.isoformat() == "2026-08-12T22:54:47-05:00"
    assert artifact.source_url == FINAL_URL and artifact.retrieved_at == NOW


def test_unresolved_branch_is_saved_but_ineligible() -> None:
    artifact = _artifact(_html().replace("Diabolist", "Invented unknown branch"))
    assert artifact.rules_branch_unresolved == 3
    assert artifact.rules[0].branch == Branch.UNRESOLVED
    assert artifact.rules[0].observability == Observability.OBSERVABLE
    assert not artifact.rules[0].comparison_eligible
    assert artifact.rules[4].branch == Branch.ALL


def test_other_specs_do_not_inherit_demonology_branch_names() -> None:
    slug = SPEC_SLUGS[("mage", "frost")]
    artifact = parse_guide(_html(), slug, slug.url, NOW)
    assert artifact.rules_branch_unresolved == 4
    assert all(rule.branch in {Branch.ALL, Branch.UNRESOLVED} for rule in artifact.rules)


def test_structural_partial_is_rejected() -> None:
    # A nested <ol> is a real structural failure (the parser cannot tell which
    # level owns which item) — unlike a spell-less item (§3E below), this
    # stays fatal.
    replacement = "<ol><li><ol><li><a href='/spell=900099/x'>Nested</a></li></ol></li>"
    with pytest.raises(PartialExtraction):
        _artifact(_html().replace("<ol>", replacement, 1))


def test_item_without_spell_is_counted_not_fatal() -> None:
    # §3E: 24/79 real-page items are opener prose with no spell link, e.g.
    # "Precast Demonbolt (4 Seconds on Pulltimer)". A <li> inside a
    # recognized list that yields no spell id must not be fatal — it emits
    # no rule and is counted in items_unrecognized, while the rest of the
    # same list still produces rules normally.
    html = _html().replace(
        "<ol>\n"
        '  <li>With <a href="/spell=900001/invented-state">Invented state</a> cast '
        '<a href="/spell=900002/invented-action">Invented action</a>, without cast '
        '<a href="/spell=900003/invented-fallback">Invented fallback</a>.</li>\n',
        "<ol>\n"
        "  <li>Precast Invented Prose (4 Seconds on Pulltimer).</li>\n"
        '  <li>With <a href="/spell=900001/invented-state">Invented state</a> cast '
        '<a href="/spell=900002/invented-action">Invented action</a>, without cast '
        '<a href="/spell=900003/invented-fallback">Invented fallback</a>.</li>\n',
    )
    artifact = _artifact(html)
    assert artifact.items_total == 10
    assert artifact.items_recognized == 9
    assert artifact.items_unrecognized == 1
    # The rest of the same list — and the rest of the guide — still produced
    # rules; the prose item is silently absent, never invented.
    assert any(900002 in rule.action_spell_ids for rule in artifact.rules)
    assert any(900004 in rule.action_spell_ids for rule in artifact.rules)
    assert not any("Invented Prose" in str(rule.action_spell_ids) for rule in artifact.rules)


def test_recognized_section_with_only_prose_items_is_counted_not_fatal() -> None:
    # §3E: lists CAN be claimed while every item in them is unrecognized
    # prose — "the page is prose", not a parser failure. That alone must not
    # reject, as long as some other recognized section (here: AoE, Opener,
    # Pre-Combat, Cooldown) still produced rules and Single Target is intact.
    html = _html().replace(
        "<ol>\n"
        '  <li>Cast <a href="/spell=900006/invented-sequence">Invented sequence</a> before '
        '<a href="/spell=900007/invented-followup">Invented followup</a>.</li>\n'
        "</ol>\n",
        "<ol>\n  <li>Build to 5 shards with Invented Prose.</li>\n</ol>\n",
    )
    artifact = _artifact(html)
    assert artifact.items_unrecognized == 1
    assert not any(900006 in rule.action_spell_ids for rule in artifact.rules)
    # The rest of Single Target (the other claimed list under the same
    # section) still produced rules — the all-prose list did not sink it.
    assert any(rule.section == Section.SINGLE_TARGET for rule in artifact.rules)


def test_single_target_claimed_but_all_prose_still_rejects() -> None:
    # §3E: SINGLE_TARGET is the core of the guide. Even though a claimed
    # list with no recognized items is not fatal in general (proven above),
    # it IS fatal when it leaves SINGLE_TARGET with zero rules overall.
    html = _html().replace(
        "<ol>\n"
        '  <li>With <a href="/spell=900001/invented-state">Invented state</a> cast '
        '<a href="/spell=900002/invented-action">Invented action</a>, without cast '
        '<a href="/spell=900003/invented-fallback">Invented fallback</a>.</li>\n'
        '  <li>Cast <a href="/spell=900004/invented-choice">Invented choice</a>/'
        '<a href="/spell=900005/invented-other">Invented other</a>.</li>\n'
        "</ol>\n"
        "<p>Soul Harvester</p><h3>Priority</h3>\n"
        "<ol>\n"
        '  <li>Cast <a href="/spell=900006/invented-sequence">Invented sequence</a> before '
        '<a href="/spell=900007/invented-followup">Invented followup</a>.</li>\n'
        "</ol>\n",
        "<ol>\n"
        "  <li>Precast Invented Prose (No Link).</li>\n"
        "  <li>Build to 5 shards with Invented Prose.</li>\n"
        "</ol>\n"
        "<p>Soul Harvester</p><h3>Priority</h3>\n"
        "<ol>\n"
        "  <li>Summon Invented Prose Tyrant.</li>\n"
        "</ol>\n",
    )
    with pytest.raises(PartialExtraction, match="Single target"):
        _artifact(html)


def test_unknown_heading_is_skipped_not_rejected() -> None:
    # §3D: an unrecognized heading (the real Wowhead page has "Sample Timeline",
    # per-cooldown h3s, etc. beyond the enumerated vocabulary) must not raise.
    # Its subtree — including any <ol> — is ignored and counted, and the rest
    # of the recognized rotation must still parse and produce rules.
    html = _html().replace(
        "<h2>Pre-Combat Check</h2>",
        '<h3>Mystery Heading</h3><ol><li>Cast <a href="/spell=900099/mystery">Mystery</a>.'
        "</li></ol><h2>Pre-Combat Check</h2>",
    )
    artifact = _artifact(html)
    assert artifact.sections_skipped == 1
    # The skipped subtree's spell never enters the artifact: this is a real
    # skip, not merely "did not raise".
    assert not any(900099 in rule.action_spell_ids for rule in artifact.rules)
    assert artifact.items_total == 9  # unchanged from the baseline fixture
    assert len(artifact.rules) > 0


def test_unknown_heading_ordered_list_is_not_unclaimed() -> None:
    # An <ol> under an unrecognized heading is out of the declared scope, not
    # the structural "unclaimed list" failure — that failure stays reserved
    # for a list with no recognized section at all (proven below).
    html = _html().replace(
        "<h2>Pre-Combat Check</h2>",
        '<h3>Mystery Heading</h3><ol><li>Cast <a href="/spell=900099/mystery">Mystery</a>.'
        "</li></ol><h2>Pre-Combat Check</h2>",
    )
    _artifact(html)  # must not raise at all
    unclaimed_html = _html().replace(
        "<h2>Best Synthetic Rotation</h2>",
        "<h2>Best Synthetic Rotation</h2><ol><li><a href='/spell=900100/x'>X</a></li></ol>",
    )
    with pytest.raises(PartialExtraction, match="Unclaimed"):
        _artifact(unclaimed_html)


def test_recognized_section_without_any_list_is_not_fatal() -> None:
    # §3F: on the real page COOLDOWN and PRE_COMBAT are narrative sections
    # with zero <ol> at all (measured: ol=0, li=0 for both). A recognized
    # heading that claims no list must NOT reject — this used to be fatal
    # ("Recognized section without any rule"), and that rule is revoked by
    # §3F after it produced two false rejections on the real page (§3D, §3E
    # were the earlier two). The artifact must still build from the sections
    # that do have lists.
    html = (
        _html()
        .replace(
            "<h2>Pre-Combat Check</h2><ol>\n"
            '  <li>Cast <a href="/spell=900013/invented-preparation">'
            "Invented preparation</a>.</li>\n"
            "</ol>\n",
            "<h2>Pre-Combat Check</h2><p>Narrative only, no list at all.</p>\n",
        )
        .replace(
            "<h2>Synthetic Major Cooldowns</h2><h3>Invented cooldown</h3><ol>\n"
            '  <li>Cast <a href="/spell=900014/invented-cooldown">Invented cooldown</a>.</li>\n'
            "</ol>\n",
            "<h2>Synthetic Major Cooldowns</h2><p>Narrative only, no list at all.</p>\n",
        )
    )
    assert "Synthetic Major Cooldowns" in html  # heading itself still present
    assert "Pre-Combat Check" in html
    artifact = _artifact(html)  # must not raise
    # Two items (pre-combat, cooldown) disappeared along with their lists;
    # everything else — including Single Target — still produced rules.
    assert artifact.items_total == 7
    assert not any(900013 in rule.action_spell_ids for rule in artifact.rules)
    assert not any(900014 in rule.action_spell_ids for rule in artifact.rules)
    assert any(rule.section == Section.SINGLE_TARGET for rule in artifact.rules)
    assert any(rule.section == Section.AOE for rule in artifact.rules)


def test_single_target_section_required() -> None:
    # SINGLE_TARGET is the core of a rotation guide: absent (never recognized)
    # or empty must reject even when every other section produced rules.
    html = _html().replace("Best Synthetic Single Target Rotation", "Best Synthetic AoE Rotation")
    with pytest.raises(PartialExtraction, match="Single target"):
        _artifact(html)


def test_unclaimed_and_empty_rotation_rejected() -> None:
    with pytest.raises(PartialExtraction, match="Unclaimed"):
        _artifact(
            _html().replace(
                "<h2>Best Synthetic Rotation</h2>",
                "<h2>Best Synthetic Rotation</h2><ol><li><a href='/spell=900100/x'>X</a></li></ol>",
            )
        )
    with pytest.raises(PartialExtraction, match="Zero"):
        _artifact(_html().split("<body>")[0] + "<body><h2>Best Synthetic Rotation</h2></body>")


@pytest.mark.parametrize(
    "marker",
    [
        "when",
        "if",
        "while",
        "unless",
        "above",
        "below",
        "during",
        "after",
        "only",
        "not",
        "with",
        "without",
        "before",
        "at",
    ],
)
def test_unknown_condition_markers_fail_closed(marker: str) -> None:
    artifact = _artifact(
        _html().replace('Cast <a href="/spell=900012', f'{marker} mystery <a href="/spell=900012')
    )
    rule = next(rule for rule in artifact.rules if rule.section == Section.OPENER)
    assert rule.condition.kind == ConditionType.UNRECOGNIZED
    assert not rule.comparison_eligible


def test_not_observable_never_exposed_as_comparison_candidate() -> None:
    rules = _artifact().rules
    reference_only = [rule for rule in rules if rule.observability == Observability.NOT_OBSERVABLE]
    assert reference_only
    assert all(not rule.comparison_eligible for rule in reference_only)
    assert any(rule.comparison_eligible for rule in rules)
    # M33 has no conversion to Finding at all; M28 owns coaching.
    assert "finding" not in RotationRule.model_fields


@pytest.mark.parametrize(
    "text",
    [
        "With <a href='/spell=900020/a'>A</a> and <a href='/spell=900021/b'>B</a> "
        "cast <a href='/spell=900022/c'>C</a>.",
        "Cast <a href='/spell=900022/c'>C</a> before <a href='/spell=900020/a'>A</a> "
        "or <a href='/spell=900021/b'>B</a>.",
    ],
)
def test_compound_subjects_are_not_flattened_into_actions(text: str) -> None:
    html = _html().replace(
        'Cast <a href="/spell=900012/invented-start">Invented start</a>.',
        text,
    )
    rule = next(rule for rule in _artifact(html).rules if rule.section == Section.OPENER)
    assert rule.condition.kind == ConditionType.UNRECOGNIZED
    assert not rule.comparison_eligible


def test_invalid_object_cannot_replace_valid_artifact(tmp_path: Path) -> None:
    store = KnowledgeStore(tmp_path)
    artifact = _artifact()
    store.write(artifact)
    path = store.path_for(SPEC)
    previous = path.read_bytes()
    with pytest.raises(ValidationError):
        store.write(artifact.model_copy(update={"items_recognized": 1}))
    assert path.read_bytes() == previous
    assert list(tmp_path.iterdir()) == [path]


def test_no_article_prose_fields_and_unknown_fields_rejected() -> None:
    assert set(RotationRule.model_fields) == {
        "section",
        "branch",
        "ordinal",
        "action_spell_ids",
        "alternative",
        "condition",
        "origin",
        "observability",
    }
    assert set(Condition.model_fields) == {
        "kind",
        "subject_spell_ids",
        "negated",
        "comparator",
        "threshold",
        "unit",
    }
    artifact = _artifact()
    payload = artifact.model_dump(mode="json")
    encoded = json.dumps(payload)
    for text in ("Invented state", "Invented fallback", "Only if", "Synthetic illustration"):
        assert text not in encoded
    assert "Synthetic rotation reference" in encoded  # mandatory provenance, not article prose
    payload["rules"][0]["text"] = "Copied article prose"
    with pytest.raises(ValidationError):
        RotationKnowledge.model_validate_json(json.dumps(payload))


@pytest.mark.parametrize(
    "field,value",
    [
        ("items_recognized", 1),
        ("rules", []),
        ("schema_version", 99),
        ("source_url", "https://example.com/rotation"),
        ("source_modified_at", "2026-09-09T00:00:00"),
        ("rules_branch_unresolved", 100),
        ("rules_condition_unrecognized", 100),
    ],
)
def test_malformed_artifacts_are_unavailable_locally(
    tmp_path: Path,
    field: str,
    value: Any,
) -> None:
    store = KnowledgeStore(tmp_path)
    payload = _artifact().model_dump(mode="json")
    payload[field] = value
    store.path_for(SPEC).write_text(json.dumps(payload), encoding="utf-8")
    snapshot = store.read(SPEC)
    assert snapshot.state == KnowledgeState.UNAVAILABLE and snapshot.artifact is None


def test_unchanged_preserves_bytes_mtime_and_creation_time(tmp_path: Path) -> None:
    store = KnowledgeStore(tmp_path)
    with _client(_html()) as client:
        source = WowheadSource(client)
        assert source.update_one(store, SPEC).status == UpdateStatus.UPDATED
        path = store.path_for(SPEC)
        previous, stat = path.read_bytes(), path.stat()
        assert source.update_one(store, SPEC).status == UpdateStatus.UNCHANGED
    assert path.read_bytes() == previous and path.stat().st_mtime_ns == stat.st_mtime_ns
    assert store.read(SPEC).state == KnowledgeState.CURRENT
    assert KnowledgeStore(tmp_path).read(SPEC).state == KnowledgeState.STALE_UNVERIFIED


def test_update_uses_atomic_replace_after_validation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = KnowledgeStore(tmp_path)
    store.write(_artifact())
    path = store.path_for(SPEC)
    previous = path.read_bytes()
    replace = store_module.os.replace
    calls = []

    def checked_replace(source: Path, destination: Path) -> None:
        assert path.read_bytes() == previous
        assert source.parent == destination.parent == tmp_path
        RotationKnowledge.model_validate_json(source.read_bytes())
        calls.append(destination)
        replace(source, destination)

    monkeypatch.setattr(store_module.os, "replace", checked_replace)
    with _client(_html().replace("900012", "900112")) as client:
        result = WowheadSource(client).update_one(store, SPEC)
    assert result.status == UpdateStatus.UPDATED and calls == [path]
    assert path.read_bytes() != previous
    assert list(tmp_path.iterdir()) == [path]


@pytest.mark.parametrize(
    "status,html,expected",
    [
        (404, "missing", UpdateStatus.UNAVAILABLE),
        (503, "error", UpdateStatus.FAILED),
        (200, "<html>Error page</html>", UpdateStatus.FAILED),
        (200, "<h2>Best Rotation</h2>", UpdateStatus.FAILED),
    ],
)
def test_source_failures_preserve_previous(
    tmp_path: Path,
    status: int,
    html: str,
    expected: UpdateStatus,
) -> None:
    store = KnowledgeStore(tmp_path)
    store.write(_artifact())
    path = store.path_for(SPEC)
    previous, stat = path.read_bytes(), path.stat()
    with _client(html, status) as client:
        result = WowheadSource(client).update_one(store, SPEC)
    assert result.status == expected and result.previous_preserved
    assert path.read_bytes() == previous and path.stat().st_mtime_ns == stat.st_mtime_ns
    assert store.read(SPEC).state == KnowledgeState.STALE_UNVERIFIED


def test_partial_and_failed_replace_preserve_previous(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = KnowledgeStore(tmp_path)
    store.write(_artifact())
    path = store.path_for(SPEC)
    previous = path.read_bytes()
    # A nested <ol> is a real structural failure (§3E leaves this fatal — a
    # spell-less item alone is no longer enough, see the dedicated tests
    # above for that non-fatal path).
    nested = "<ol><li><ol><li><a href='/spell=900099/x'>Nested</a></li></ol></li>"
    with _client(_html().replace("<ol>", nested, 1)) as client:
        assert WowheadSource(client).update_one(store, SPEC).status == UpdateStatus.FAILED
    assert path.read_bytes() == previous

    def fail_replace(*_args: object) -> None:
        raise OSError("Synthetic disk failure")

    monkeypatch.setattr(store_module.os, "replace", fail_replace)
    with _client(_html().replace("900012", "900112")) as client:
        assert WowheadSource(client).update_one(store, SPEC).status == UpdateStatus.FAILED
    assert path.read_bytes() == previous and list(tmp_path.iterdir()) == [path]


def test_partial_success_and_redirect_url_for_full_inventory(tmp_path: Path) -> None:
    requests: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        if "/devourer/" in request.url.path:
            return httpx.Response(404)
        if request.url.path.endswith("/rotation"):
            return httpx.Response(
                302, headers={"location": str(request.url) + "-cooldowns-pve-dps"}
            )
        return httpx.Response(200, text=_html(), headers={"content-type": "text/html"})

    store = KnowledgeStore(tmp_path)
    devourer = SPEC_SLUGS[("demonhunter", "devourer")]
    old = parse_guide(_html(), devourer, devourer.url, NOW)
    store.write(old)
    old_path = store.path_for(SpecId("Demon Hunter", "Devourer"))
    previous = old_path.read_bytes()
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        results = WowheadSource(client).update_all(store)
    assert len(results) == 26 and len(requests) == 51  # simulated requests only
    assert sum(result.status == UpdateStatus.UPDATED for result in results) == 25
    unavailable = [result for result in results if result.status == UpdateStatus.UNAVAILABLE]
    assert len(unavailable) == 1 and unavailable[0].previous_preserved
    assert old_path.read_bytes() == previous
    artifact = store.read(SPEC).artifact
    assert artifact is not None and artifact.source_url == FINAL_URL
    assert len(list(tmp_path.glob("*.json"))) == 26


def test_unsupported_and_cross_origin_redirect_never_fetch_other_source(tmp_path: Path) -> None:
    seen = []

    def respond(request: httpx.Request) -> httpx.Response:
        seen.append(request.url)
        return httpx.Response(302, headers={"location": "https://example.com/rotation"})

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        source = WowheadSource(client)
        store = KnowledgeStore(tmp_path)
        assert (
            source.update_one(store, SpecId("Mage", "Unknown")).status == UpdateStatus.UNSUPPORTED
        )
        assert not seen
        assert source.update_one(store, SPEC).status == UpdateStatus.FAILED
        assert len(seen) == 1


@pytest.mark.parametrize("error", [httpx.ReadTimeout("synthetic"), RuntimeError("synthetic")])
def test_transport_failure_preserves_previous_without_retry(
    tmp_path: Path,
    error: Exception,
) -> None:
    store = KnowledgeStore(tmp_path)
    store.write(_artifact())
    path = store.path_for(SPEC)
    previous = path.read_bytes()
    calls = []

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(request.url)
        raise error

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        result = WowheadSource(client).update_one(store, SPEC)
    assert result.status == UpdateStatus.FAILED and result.previous_preserved
    assert len(calls) == 1 and path.read_bytes() == previous


def test_local_boundary_transitive_imports_do_not_reach_network(tmp_path: Path) -> None:
    source_root = Path(__file__).parents[2] / "src"
    pending = ["botgitgud.knowledge", "botgitgud.knowledge.store"]
    visited = set()
    forbidden = {"httpx", "requests", "urllib.request", "aiohttp", "socket"}
    while pending:
        module = pending.pop()
        if module in visited:
            continue
        visited.add(module)
        path = source_root.joinpath(*module.split(".")).with_suffix(".py")
        if not path.exists():
            path = source_root.joinpath(*module.split("."), "__init__.py")
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            imported = []
            if isinstance(node, ast.Import):
                imported = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported = [node.module]
            for name in imported:
                assert name not in forbidden and not name.endswith("wowhead_source")
                if name.startswith("botgitgud."):
                    pending.append(name)
    assert "botgitgud.knowledge.rotation_knowledge" in visited
    assert KnowledgeStore(tmp_path).read(SPEC).state == KnowledgeState.UNAVAILABLE
