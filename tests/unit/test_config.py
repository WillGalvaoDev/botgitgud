from __future__ import annotations

import pytest
from pydantic import ValidationError

from botgitgud.config import Settings


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


def test_secrets_never_leak_in_str_repr() -> None:
    settings = _settings()
    assert "secret" not in str(settings.wcl_client_secret)
    assert "secret2" not in str(settings.blizzard_client_secret)
    assert "d" * 10 not in str(settings.discord_token)


def test_secret_value_still_retrievable_via_get_secret_value() -> None:
    settings = _settings(wcl_client_secret="the-real-secret")
    assert settings.wcl_client_secret.get_secret_value() == "the-real-secret"


def test_defaults_match_t08_revised_cohort_values_not_the_stale_t11_draft() -> None:
    """docs/desvios.md D-10: T1.1's own pseudocode has stale cohort defaults
    (10/30/±7%) that predate the T0.8 correction. Settings must match what
    T0.6/T0.8 actually implemented and tested (8/20/±35%/±12%), not the draft.
    """
    settings = _settings()
    assert settings.cohort_min_hard == 8
    assert settings.cohort_min_warn == 20
    assert settings.sanity_band_pct == 0.35
    assert settings.positional_band_pct == 0.12
    assert settings.positional_min_n == 8


def test_missing_required_credential_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    # Other test modules import bot.py/legacy_bot at collection or run time,
    # which calls load_dotenv() — that permanently populates os.environ for
    # the rest of this pytest process. _env_file=None only disables reading
    # the .env FILE; it does not stop pydantic-settings from still reading
    # real OS environment variables. Without clearing them here, this test
    # passes in isolation but fails when the full suite runs in a different
    # order (caught by running `pytest -q` for the whole project, not just
    # this file).
    for var in (
        "DISCORD_TOKEN",
        "WCL_CLIENT_ID",
        "WCL_CLIENT_SECRET",
        "BLIZZARD_CLIENT_ID",
        "BLIZZARD_CLIENT_SECRET",
    ):
        monkeypatch.delenv(var, raising=False)

    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,  # type: ignore[call-arg]
            wcl_client_id="id",
            wcl_client_secret="secret",
            blizzard_client_id="id2",
            blizzard_client_secret="secret2",
        )  # type: ignore[call-arg]


def test_settings_field_values_match_fase0_constants() -> None:
    """Sanity-check a few cross-module constants stay in sync in spirit,
    even though Fase 0 modules keep their own local copies for now
    (T1.6 is what rewires call sites to read from Settings).
    """
    from botgitgud.analysis.alignment import align

    settings = _settings()
    # align()'s own default must still match Settings' gap_penalty_s.
    a_default = align([0.0], [100.0])
    a_explicit = align([0.0], [100.0], gap_penalty=settings.gap_penalty_s)
    assert a_default.total_cost == a_explicit.total_cost


# -- T1.5: settings_hash() -----------------------------------------------------


def test_settings_hash_deterministic_for_equal_settings() -> None:
    assert _settings().settings_hash() == _settings().settings_hash()


def test_settings_hash_changes_when_a_non_secret_field_changes() -> None:
    base = _settings().settings_hash()
    changed = _settings(cohort_min_hard=99).settings_hash()
    assert base != changed


def test_settings_hash_ignores_credential_values() -> None:
    """settings_hash exists to detect drift in analysis parameters, not to
    fingerprint credentials — two Settings differing only in secrets must
    hash identically, and the raw secret values must never appear in the
    hash input at all (they're excluded before hashing, not merely hashed).
    """
    a = _settings(wcl_client_secret="secret-one").settings_hash()
    b = _settings(wcl_client_secret="secret-two").settings_hash()
    assert a == b
