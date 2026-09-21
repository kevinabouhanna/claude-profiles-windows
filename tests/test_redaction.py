"""Tests for secret scrubbing.

Two halves: known secret shapes must be destroyed, and legitimate values that
happen to look opaque (organization UUIDs, ISO timestamps) must survive.
"""

from __future__ import annotations

import pytest

from claude_profiles.services.redaction import (
    REDACTED,
    contains_secret,
    mask_email,
    mask_emails,
    redact,
    redact_exception,
    safe_for_log,
)

SECRETS = [
    "sk-ant-api03-AbCdEf0123456789xyz",
    "sk-ant-oat01-ZZZZZZZZZZZZZZZZZZZZZZ",
    "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dBjftJeZ4CVPmB92K27uhbUJU1p1r_wW1g",
    "Bearer abcdefghijklmnopqrstuvwxyz123456",
]


@pytest.mark.parametrize("secret", SECRETS)
def test_known_secret_shapes_are_removed(secret):
    text = f"request failed with {secret} attached"
    result = redact(text)
    assert secret not in result
    assert REDACTED in result or "Bearer [redacted]" in result


@pytest.mark.parametrize(
    "key",
    [
        "access_token",
        "refresh_token",
        "id_token",
        "session_token",
        "api_key",
        "client_secret",
        "oauth-token",
        "password",
        "credential",
        "cookie",
    ],
)
def test_secret_assignments_are_blanked(key):
    text = f'{{"{key}": "hunter2-abcdefghijklmnop"}}'
    result = redact(text)
    assert "hunter2" not in result
    assert key in result  # the key stays; only the value goes


def test_authorization_header_is_removed():
    text = "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.eyJhIjoxfQ.sig"
    result = redact(text)
    assert "eyJ" not in result
    assert "Authorization" in result


def test_credentials_path_is_replaced():
    text = r"could not open C:\Users\someone\.claude\.credentials.json"
    result = redact(text)
    assert ".credentials.json" not in result
    assert "[credentials-path]" in result


def test_multiple_secrets_in_one_string():
    text = (
        'Authorization: Bearer eyJhbGciOiJI.eyJzdWIiOiIxMjM0.SflKxwRJSM '
        'key="sk-ant-api03-ABCDEF123456" refresh_token="rt-9876543210abcdef"'
    )
    result = redact(text)
    for fragment in ("eyJ", "sk-ant", "rt-9876543210"):
        assert fragment not in result


def test_redaction_is_idempotent():
    text = 'token="sk-ant-api03-ABCDEF123456"'
    once = redact(text)
    assert redact(once) == once


def test_contains_secret_detects_raw_and_clears_after_redaction():
    raw = 'access_token="sk-ant-api03-ABCDEF123456"'
    assert contains_secret(raw) is True
    assert contains_secret(redact(raw)) is False


def test_contains_secret_ignores_ordinary_text():
    assert contains_secret("Switched from Personal to Work at 14:32") is False
    assert contains_secret("Usage refreshed") is False
    assert contains_secret(None) is False


# --- no false positives ---------------------------------------------------


def test_organization_uuid_survives():
    uuid = "6ba7b810-9dad-11d1-80b4-00c04fd430c8"
    assert uuid in redact(f"organizationUuid {uuid}", aggressive=True)


def test_iso_timestamps_survive():
    text = "resets at 2026-09-22T18:00:00Z"
    assert redact(text, aggressive=True) == text


def test_ordinary_activity_lines_are_untouched():
    for line in (
        "Switched from Personal to Work at 14:32",
        "Usage refreshed",
        "Work requires re-authentication",
        "5h usage 88% - resets in 0h 41m",
    ):
        assert redact(line) == line


def test_aggressive_mode_blanks_long_opaque_blobs():
    blob = "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8S9t0U1v2"
    assert blob not in redact(f"value {blob}", aggressive=True)
    # But not in normal mode, which would damage benign identifiers.
    assert blob in redact(f"value {blob}")


# --- exceptions and emails ------------------------------------------------


def test_redact_exception_drops_secret_and_keeps_type():
    exc = RuntimeError('failed: access_token="sk-ant-api03-ABCDEF123456789"')
    result = redact_exception(exc)
    assert "RuntimeError" in result
    assert "sk-ant" not in result


def test_redact_exception_without_message():
    assert redact_exception(ValueError()) == "ValueError"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("someone@example.invalid", "s***@example.invalid"),
        ("a@b.invalid", "a***@b.invalid"),
        ("", ""),
        ("not-an-email", ""),
        (None, ""),
    ],
)
def test_mask_email(raw, expected):
    assert mask_email(raw) == expected


def test_mask_emails_in_free_text():
    result = mask_emails("switched to someone@example.invalid now")
    assert "someone@example.invalid" not in result
    assert "s***@example.invalid" in result


def test_safe_for_log_applies_both_passes():
    text = 'user someone@example.invalid token="sk-ant-api03-ABCDEF123456"'
    result = safe_for_log(text)
    assert "someone@example.invalid" not in result
    assert "sk-ant" not in result


def test_redact_handles_empty_and_none():
    assert redact(None) == ""
    assert redact("") == ""
    assert safe_for_log(None) == ""
