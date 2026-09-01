from __future__ import annotations

import pytest

from botgitgud.bot.report_url import (
    ReportPublicBaseUrlError,
    build_report_url,
    validate_report_public_base_url,
)

_TOKEN = "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P" + "q6r"  # 43-char urlsafe token shape


# -- 1/2/3: valid https/http base URLs -------------------------------------------------


def test_public_base_url_config_accepts_valid_https() -> None:
    assert validate_report_public_base_url("https://example.com") == "https://example.com"


def test_local_http_url_accepted_for_tests() -> None:
    assert validate_report_public_base_url("http://127.0.0.1:12345") == "http://127.0.0.1:12345"


def test_https_with_port_accepted() -> None:
    assert validate_report_public_base_url("https://x.example:8443") == "https://x.example:8443"


# -- 4: trailing slash normalized --------------------------------------------------------


def test_trailing_slash_is_normalized() -> None:
    assert validate_report_public_base_url("https://x.example/") == "https://x.example"


def test_trailing_slash_never_produces_double_slash_in_final_url() -> None:
    url = build_report_url("https://x.example/", "tok")
    assert url == "https://x.example/r/tok"
    assert "//r/" not in url.split("://", 1)[1]


# -- 5: javascript/data/file rejected ------------------------------------------------------


@pytest.mark.parametrize(
    "hostile",
    [
        "javascript:alert(1)",
        "data:text/html,<script>alert(1)</script>",
        "file:///etc/passwd",
        "ftp://example.com",
    ],
)
def test_hostile_or_disallowed_schemes_rejected(hostile: str) -> None:
    with pytest.raises(ReportPublicBaseUrlError):
        validate_report_public_base_url(hostile)


def test_missing_base_url_rejected() -> None:
    with pytest.raises(ReportPublicBaseUrlError):
        validate_report_public_base_url(None)
    with pytest.raises(ReportPublicBaseUrlError):
        validate_report_public_base_url("")


def test_scheme_only_no_host_rejected() -> None:
    with pytest.raises(ReportPublicBaseUrlError):
        validate_report_public_base_url("https://")


# -- 6: query rejected ----------------------------------------------------------------------


def test_query_rejected() -> None:
    with pytest.raises(ReportPublicBaseUrlError):
        validate_report_public_base_url("https://x.example?x=1")


# -- 7: fragment rejected -------------------------------------------------------------------


def test_fragment_rejected() -> None:
    with pytest.raises(ReportPublicBaseUrlError):
        validate_report_public_base_url("https://x.example#frag")


# -- 8: userinfo rejected -------------------------------------------------------------------


def test_userinfo_rejected() -> None:
    with pytest.raises(ReportPublicBaseUrlError):
        validate_report_public_base_url("https://user:pass@x.example")


def test_non_trivial_path_rejected() -> None:
    with pytest.raises(ReportPublicBaseUrlError):
        validate_report_public_base_url("https://x.example/sub/path")


# -- 9/10: build_report_url correctness + full token preservation ---------------------------


def test_build_report_url_correct_shape() -> None:
    assert build_report_url("https://x.example", _TOKEN) == f"https://x.example/r/{_TOKEN}"


def test_token_preserved_integrally_no_truncation_no_encoding() -> None:
    url = build_report_url("https://x.example", _TOKEN)
    assert url.endswith(f"/r/{_TOKEN}")
    assert len(url.rsplit("/r/", 1)[1]) == len(_TOKEN)


def test_build_report_url_never_contains_query_or_fragment() -> None:
    url = build_report_url("https://x.example", _TOKEN)
    assert "?" not in url
    assert "#" not in url


def test_build_report_url_is_deterministic() -> None:
    a = build_report_url("https://x.example", _TOKEN)
    b = build_report_url("https://x.example", _TOKEN)
    assert a == b


def test_build_report_url_propagates_base_validation_failure() -> None:
    with pytest.raises(ReportPublicBaseUrlError):
        build_report_url("javascript:alert(1)", _TOKEN)
