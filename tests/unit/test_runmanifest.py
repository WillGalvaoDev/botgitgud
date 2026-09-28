from __future__ import annotations

import re
import subprocess

import pytest

from botgitgud.config import Settings
from botgitgud.runmanifest import build_run_manifest, get_code_version


def _settings(**overrides: object) -> Settings:
    defaults: dict[str, object] = {
        "discord_token": "d" * 10,
        "wcl_client_id": "id",
        "wcl_client_secret": "secret",
        "blizzard_client_id": "id2",
        "blizzard_client_secret": "secret2",
    }
    defaults.update(overrides)
    return Settings(_env_file=None, **defaults)  # type: ignore[arg-type, call-arg]


def test_get_code_version_returns_short_git_hash_in_this_repo() -> None:
    version = get_code_version()
    assert re.fullmatch(r"[0-9a-f]{4,40}", version), version


def test_build_run_manifest_fills_every_field() -> None:
    manifest = build_run_manifest(
        cohort_id="deadbeefdeadbeef", n_members=17, wcl_partition=4, settings=_settings()
    )
    assert manifest.cohort_id == "deadbeefdeadbeef"
    assert manifest.n_members == 17
    assert manifest.wcl_partition == 4
    assert manifest.code_version == get_code_version()
    assert manifest.settings_hash == _settings().settings_hash()


def test_build_run_manifest_defaults_stream_availability_version_to_unknown() -> None:
    # M3.1 is local (docs/m3-1-specification.md): pipeline.py does not wire
    # this yet (M3.4). Changing this default is exactly the M3.4 signal.
    manifest = build_run_manifest(
        cohort_id="x", n_members=1, wcl_partition=None, settings=_settings()
    )
    assert manifest.stream_availability_version == "unknown"


def test_build_run_manifest_accepts_none_partition() -> None:
    manifest = build_run_manifest(
        cohort_id="x", n_members=1, wcl_partition=None, settings=_settings()
    )
    assert manifest.wcl_partition is None


def test_get_code_version_falls_back_to_unknown_when_git_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _raise(*args: object, **kwargs: object) -> None:
        raise FileNotFoundError("git not found")

    monkeypatch.setattr(subprocess, "run", _raise)
    assert get_code_version() == "unknown"
