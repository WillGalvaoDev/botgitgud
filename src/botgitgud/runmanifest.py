"""T1.5 — deterministic run identity (docs/implementacao.md T1.5): every
rendered report and every persisted cohort carries a RunManifest so a
result can be traced back to the exact cohort, code, and settings that
produced it.
"""

from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from pathlib import Path

from botgitgud.config import Settings
from botgitgud.domain.models import RunManifest

_UNKNOWN_VERSION = "unknown"


def get_code_version() -> str:
    """Best-effort short git hash of HEAD. Never raises: falls back to
    "unknown" outside a git checkout (e.g. a packaged install without a
    .git directory, or git missing from PATH) so a missing git binary
    never breaks report generation.
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=Path(__file__).resolve().parent,
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return _UNKNOWN_VERSION
    version = result.stdout.strip()
    return version or _UNKNOWN_VERSION


def build_run_manifest(
    *,
    cohort_id: str,
    n_members: int,
    wcl_partition: int | None,
    settings: Settings,
    reference_n_quantitative: int = 0,
) -> RunManifest:
    return RunManifest(
        cohort_id=cohort_id,
        code_version=get_code_version(),
        generated_at=datetime.now(UTC),
        n_members=n_members,
        wcl_partition=wcl_partition,
        settings_hash=settings.settings_hash(),
        measurement_input_version="measurement-input-v1",
        damage_comparison_version="damage-comparison-v2",
        reference_n_quantitative=reference_n_quantitative,
    )
