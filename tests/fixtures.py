"""Canned ``cswap --json`` payloads covering every contract state.

All addresses use the reserved ``.invalid`` TLD so no real account data appears
in the test suite.
"""

from __future__ import annotations

import json
from typing import Any

PERSONAL_EMAIL = "personal@example.invalid"
WORK_EMAIL = "work@example.invalid"


def _usage(five: float, seven: float) -> dict[str, Any]:
    return {
        "fiveHour": {
            "pct": five,
            "resetsAt": "2026-09-22T18:00:00Z",
            "countdown": "2h 13m",
            "clock": "18:00",
        },
        "sevenDay": {
            "pct": seven,
            "resetsAt": "2026-09-25T09:00:00Z",
            "countdown": "3d 04h",
            "clock": "09:00",
            "expectedPct": seven - 6,
            "aheadOfPace": True,
            "projectedExhaustionAt": "2026-09-24T12:00:00Z",
            "willLastToReset": True,
        },
        "scoped": [
            {
                "pct": 18.0,
                "name": "Opus",
                "resetsAt": "2026-09-22T18:00:00Z",
                "countdown": "2h 13m",
            }
        ],
    }


VALID_LIST: dict[str, Any] = {
    "schemaVersion": 1,
    "activeAccountNumber": 1,
    "accounts": [
        {
            "number": 1,
            "email": PERSONAL_EMAIL,
            "alias": "personal",
            "organizationName": "Personal",
            "organizationUuid": "3f2504e0-4f89-11d3-9a0c-0305e82c3301",
            "isOrganization": False,
            "active": True,
            "usageStatus": "ok",
            "usage": _usage(42.0, 63.0),
            "usageFetchedAt": "2026-09-22T15:47:00Z",
            "usageAgeSeconds": 3.2,
        },
        {
            "number": 2,
            "email": WORK_EMAIL,
            "alias": "work",
            "organizationName": "Example Org",
            "organizationUuid": "6ba7b810-9dad-11d1-80b4-00c04fd430c8",
            "isOrganization": True,
            "active": False,
            "usageStatus": "ok",
            "usage": _usage(74.0, 51.0),
            "usageFetchedAt": "2026-09-22T15:47:00Z",
            "usageAgeSeconds": 3.4,
        },
    ],
}

# A refresh failed, but claude-swap retained the previous reading.
STALE_LIST: dict[str, Any] = {
    "schemaVersion": 1,
    "activeAccountNumber": 1,
    "accounts": [
        {
            "number": 2,
            "email": WORK_EMAIL,
            "alias": "work",
            "active": False,
            "usageStatus": "ok",
            "usage": None,
            "lastGoodUsage": _usage(74.0, 51.0),
            "lastGoodFetchedAt": "2026-09-22T15:24:00Z",
            "lastGoodAgeSeconds": 1380.0,
        }
    ],
}

UNAVAILABLE_LIST: dict[str, Any] = {
    "schemaVersion": 1,
    "activeAccountNumber": 1,
    "accounts": [
        {
            "number": 2,
            "email": WORK_EMAIL,
            "alias": "work",
            "active": False,
            "usageStatus": "unavailable",
            "usage": None,
            "lastGoodUsage": _usage(74.0, 51.0),
            "lastGoodFetchedAt": "2026-09-22T15:39:00Z",
            "lastGoodAgeSeconds": 480.0,
            "usageError": "rate limited by upstream",
            "usageRetryAt": "2026-09-22T15:51:00Z",
        }
    ],
}

EXPIRED_LIST: dict[str, Any] = {
    "schemaVersion": 1,
    "activeAccountNumber": 1,
    "accounts": [
        {
            "number": 2,
            "email": WORK_EMAIL,
            "alias": "work",
            "active": False,
            "usageStatus": "relogin_required",
            "usage": None,
            "loginExpiresAt": "2026-09-22T10:00:00Z",
        }
    ],
}

EMPTY_LIST: dict[str, Any] = {
    "schemaVersion": 1,
    "activeAccountNumber": None,
    "accounts": [],
}

UNKNOWN_SCHEMA_LIST: dict[str, Any] = {**VALID_LIST, "schemaVersion": 99}

ERROR_ENVELOPE: dict[str, Any] = {
    "schemaVersion": 1,
    "error": {"type": "AccountNotFound", "message": "No account matches 'work'."},
}

VALID_STATUS: dict[str, Any] = {
    "schemaVersion": 1,
    "active": {"email": WORK_EMAIL, "managed": True},
}

UNMANAGED_STATUS: dict[str, Any] = {
    "schemaVersion": 1,
    "active": {"email": WORK_EMAIL, "managed": False},
}

MALFORMED_TRUNCATED = '{"schemaVersion": 1, "accounts": [{"email": "a@b.invalid"'
MALFORMED_NOT_JSON = "cswap: could not reach the credential store\n"
NOISE_THEN_JSON = "warning: cache entry ignored\n" + json.dumps(VALID_LIST)


def dumps(payload: dict[str, Any]) -> str:
    return json.dumps(payload, indent=2)
