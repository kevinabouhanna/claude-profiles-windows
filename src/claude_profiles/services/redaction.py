"""Secret scrubbing for every string that reaches a log, dialog, or activity entry.

This module is the single chokepoint for diagnostics. The rule the whole app
follows: raw subprocess stdout/stderr is never logged, and anything that *is*
recorded passes through :func:`redact` first.

Patterns are ordered most-specific first so that a value matched by a precise
rule is never re-mangled by a looser one. The deliberately loose "opaque blob"
rule is opt-in (``aggressive=True``) and used only for exception text, because
it would otherwise damage legitimate values such as organization UUIDs.
"""

from __future__ import annotations

import re
from typing import Final

REDACTED: Final = "[redacted]"

# --- Specific, high-confidence secret shapes ------------------------------

_ANTHROPIC_KEY = re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{6,}")
_JWT = re.compile(r"\beyJ[A-Za-z0-9_\-]{6,}(?:\.[A-Za-z0-9_\-]+){1,2}")
_BEARER = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{6,}")
_AUTHORIZATION = re.compile(r"(?i)\bauthorization\b\s*[:=]\s*[\"']?[^\"'\s,}]+")

# key/value assignments such as  "access_token": "abc"  or  refresh-token=abc
_SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b("
    r"(?:access|refresh|id|session|api|auth|client|setup|oauth)[_\-]?(?:token|key|secret)"
    r"|token|secret|password|passwd|credential|cookie|apikey"
    r")\b(\"?\s*[:=]\s*)(\"?)([^\"',}\s]+)"
)

# Paths to Claude Code's credential store. The app never reads these, but a
# third-party error string could still quote one.
_CREDENTIAL_PATH = re.compile(
    r"(?i)(?:[A-Za-z]:)?[\\/][^\s\"']*\.credentials\.json"
)

# Opaque high-entropy blobs. Applied only in aggressive mode. The 40-char floor
# keeps UUIDs (36 chars) and ISO8601 timestamps intact.
_OPAQUE_BLOB = re.compile(r"\b[A-Za-z0-9_\-]{40,}\b")
_UUID = re.compile(
    r"\A[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\Z"
)

_EMAIL = re.compile(r"\b([A-Za-z0-9._%+\-]+)@([A-Za-z0-9.\-]+\.[A-Za-z]{2,})\b")


def _blank_assignment(match: re.Match[str]) -> str:
    key, sep, quote, _value = match.groups()
    return f"{key}{sep}{quote}{REDACTED}"


def _blank_blob(match: re.Match[str]) -> str:
    value = match.group(0)
    # Preserve UUIDs; they identify organizations, not sessions.
    return value if _UUID.match(value) else REDACTED


def redact(text: str | None, *, aggressive: bool = False) -> str:
    """Strip anything that looks like a credential from ``text``.

    ``aggressive`` additionally replaces opaque 40+ character blobs, which is
    appropriate for exception text but too blunt for ordinary UI strings.
    """
    if not text:
        return ""

    result = str(text)
    result = _CREDENTIAL_PATH.sub("[credentials-path]", result)
    result = _ANTHROPIC_KEY.sub(REDACTED, result)
    result = _JWT.sub(REDACTED, result)
    result = _BEARER.sub(f"Bearer {REDACTED}", result)
    result = _AUTHORIZATION.sub(f"Authorization: {REDACTED}", result)
    result = _SECRET_ASSIGNMENT.sub(_blank_assignment, result)
    if aggressive:
        result = _OPAQUE_BLOB.sub(_blank_blob, result)
    return result


def redact_exception(exc: BaseException) -> str:
    """Render an exception as a short, secret-free string.

    Only the type name and the redacted message survive; tracebacks and any
    embedded command output are dropped entirely.
    """
    message = redact(str(exc), aggressive=True).strip()
    name = type(exc).__name__
    return f"{name}: {message}" if message else name


def mask_email(email: str | None) -> str:
    """``kevin@example.com`` -> ``k***@example.com`` for on-disk logs.

    The UI renders full addresses; only persisted records are masked.
    """
    if not email or "@" not in email:
        return ""
    local, _, domain = email.partition("@")
    if not local:
        return f"***@{domain}"
    return f"{local[0]}***@{domain}"


def mask_emails(text: str | None) -> str:
    """Mask every email address appearing in a free-form string."""
    if not text:
        return ""
    return _EMAIL.sub(lambda m: mask_email(f"{m.group(1)}@{m.group(2)}"), str(text))


def safe_for_log(text: str | None) -> str:
    """Full treatment for anything written to disk: redact, then mask emails."""
    return mask_emails(redact(text, aggressive=True))


def contains_secret(text: str | None) -> bool:
    """True if ``text`` still carries something secret-shaped.

    Defined as "redaction would change this string". Testing idempotence rather
    than re-listing the patterns means this helper cannot drift out of sync with
    :func:`redact`, and it does not false-positive on text that has already been
    redacted (where the placeholder itself sits in a ``key=value`` position).

    Used by tests as a safety net over every generated log line.
    """
    if not text:
        return False
    value = str(text)
    return redact(value) != value
