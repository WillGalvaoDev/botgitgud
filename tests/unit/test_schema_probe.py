from __future__ import annotations

import httpx
import pytest

from botgitgud.wcl.schema_probe import (
    FIELD_TABLE,
    CheckResult,
    probe,
    render_markdown,
)


def test_field_table_covers_every_t01_item() -> None:
    """The 8 rows demanded by T0.1's confirmation table must all be present."""
    assert len(FIELD_TABLE) == 8
    for check in FIELD_TABLE:
        assert check.path
        assert check.purpose
        assert check.depends_on
        assert check.introspect is not None or check.live_verified_note is not None


def test_render_markdown_has_one_row_per_check() -> None:
    results = [CheckResult(check=c, verdict="ok", detail="x") for c in FIELD_TABLE]
    md = render_markdown(results)
    row_lines = [line for line in md.splitlines() if line.startswith("| `")]
    assert len(row_lines) == len(FIELD_TABLE)
    for check in FIELD_TABLE:
        assert check.path.split(" ")[0].split("(")[0].split("{")[0] in md


def test_render_markdown_marks_missing_with_x() -> None:
    check = FIELD_TABLE[0]
    results = [CheckResult(check=check, verdict="missing", detail="campo sumiu")]
    md = render_markdown(results)
    assert "❌" in md
    assert "campo sumiu" in md


def test_probe_reports_missing_field_without_hitting_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A field genuinely absent from the schema must surface as 'missing', not silently pass."""

    def fake_introspect_type_fields(_token: str, type_name: str) -> set[str] | None:
        if type_name == "RateLimitData":
            return {"limitPerHour"}  # pointsSpentThisHour / pointsResetIn missing on purpose
        return {"anything"}

    def fake_introspect_field_args(_token: str, _parent: str, _field: str) -> set[str] | None:
        return {"className", "specName", "metric", "page", "difficulty", "partition", "bracket"}

    monkeypatch.setattr(
        "botgitgud.wcl.schema_probe._introspect_type_fields", fake_introspect_type_fields
    )
    monkeypatch.setattr(
        "botgitgud.wcl.schema_probe._introspect_field_args", fake_introspect_field_args
    )

    results = probe(token="fake-token")
    rate_limit_result = next(r for r in results if r.check is FIELD_TABLE[0])
    assert rate_limit_result.verdict == "missing"
    assert "pointsSpentThisHour" in rate_limit_result.missing_items


@pytest.mark.network
def test_probe_against_live_api_has_zero_missing() -> None:
    """Regression guard: schema drift should fail this test, not production code."""
    try:
        results = probe()
    except httpx.HTTPError:
        pytest.skip("sem acesso à API ao vivo neste ambiente")
    missing = [r for r in results if r.verdict == "missing"]
    assert not missing, [(r.check.path, r.detail) for r in missing]
