"""A credential-free stand-in for :class:`CswapClient`.

The mock builds the same JSON documents the real CLI emits and feeds them
through the same ``models.parse`` code paths, so demo mode and most tests
exercise production parsing rather than a parallel implementation.

Email addresses use the reserved ``.invalid`` TLD (RFC 2606) so no real address
ever appears in source, fixtures, or screenshots.
"""

from __future__ import annotations

import itertools
import math
import time
from datetime import UTC, datetime, timedelta
from typing import Any

from ..models import AccountList, ActiveStatus, SwitchOutcome
from .cswap_client import CswapError, CswapErrorKind

PERSONAL_EMAIL = "personal@example.invalid"
WORK_EMAIL = "work@example.invalid"

SCENARIOS = (
    "healthy",
    "work_reauth",
    "work_stale",
    "work_unavailable",
    "high_usage",
    "no_accounts",
    "unknown_schema",
)


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _countdown(delta: timedelta) -> str:
    total = max(0, int(delta.total_seconds()))
    days, rem = divmod(total, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    if days:
        return f"{days}d {hours:02d}h"
    return f"{hours}h {minutes:02d}m"


def _window(pct: float, resets_in: timedelta, *, name: str | None = None) -> dict[str, Any]:
    resets_at = datetime.now(UTC) + resets_in
    window: dict[str, Any] = {
        "pct": round(pct, 1),
        "resetsAt": _iso(resets_at),
        "countdown": _countdown(resets_in),
        "clock": resets_at.astimezone().strftime("%H:%M"),
    }
    if name:
        window["name"] = name
    return window


class MockCswapClient:
    """Implements the :class:`CswapBackend` protocol with synthetic data."""

    def __init__(self, scenario: str = "healthy") -> None:
        if scenario not in SCENARIOS:
            raise ValueError(f"unknown scenario: {scenario}")
        self.scenario = scenario
        self._started = time.monotonic()
        self._active_alias = "personal"
        # Test hooks.
        self.calls: list[str] = []
        self.fail_next_list: CswapError | None = None
        self.fail_next_switch: CswapError | None = None
        self.switch_delay = 0.0
        self._counter = itertools.count()

    @property
    def is_available(self) -> bool:
        return True

    @property
    def executable(self) -> str | None:
        return "<mock>"

    # -- synthetic payloads -------------------------------------------------

    def _drift(self, base: float, period: float, span: float) -> float:
        """A slow sine so the demo UI visibly changes between refreshes."""
        elapsed = time.monotonic() - self._started
        return max(0.0, min(100.0, base + span * math.sin(elapsed / period)))

    def _usage(self, base_five: float, base_seven: float) -> dict[str, Any]:
        five = self._drift(base_five, 90, 4)
        seven = self._drift(base_seven, 300, 2)
        usage: dict[str, Any] = {
            "fiveHour": _window(five, timedelta(hours=2, minutes=13)),
            "sevenDay": {
                **_window(seven, timedelta(days=3, hours=4)),
                "expectedPct": round(seven - 6, 1),
                "aheadOfPace": True,
                "projectedExhaustionAt": _iso(datetime.now(UTC) + timedelta(days=2, hours=9)),
                "willLastToReset": seven < 80,
            },
            "scoped": [
                _window(self._drift(18, 120, 3), timedelta(hours=2, minutes=13), name="Opus")
            ],
        }
        return usage

    def _account(self, alias: str) -> dict[str, Any]:
        is_personal = alias == "personal"
        account: dict[str, Any] = {
            "number": 1 if is_personal else 2,
            "email": PERSONAL_EMAIL if is_personal else WORK_EMAIL,
            "alias": alias,
            "organizationName": "Personal" if is_personal else "Example Org",
            "organizationUuid": (
                "3f2504e0-4f89-11d3-9a0c-0305e82c3301"
                if is_personal
                else "6ba7b810-9dad-11d1-80b4-00c04fd430c8"
            ),
            "isOrganization": not is_personal,
            "active": self._active_alias == alias,
            "usageStatus": "ok",
            "usage": self._usage(42, 63) if is_personal else self._usage(74, 51),
            "usageFetchedAt": _iso(datetime.now(UTC)),
            "usageAgeSeconds": 3.2,
        }

        if alias == "work":
            if self.scenario == "work_reauth":
                account["usageStatus"] = "relogin_required"
                account["usage"] = None
                account.pop("usageFetchedAt", None)
                account.pop("usageAgeSeconds", None)
                account["loginExpiresAt"] = _iso(datetime.now(UTC) - timedelta(hours=5))
            elif self.scenario == "work_stale":
                # The shape cswap emits when a refresh failed but a previous
                # reading is retained.
                account["usage"] = None
                account.pop("usageFetchedAt", None)
                account.pop("usageAgeSeconds", None)
                account["lastGoodUsage"] = self._usage(74, 51)
                account["lastGoodFetchedAt"] = _iso(datetime.now(UTC) - timedelta(minutes=23))
                account["lastGoodAgeSeconds"] = 1380.0
            elif self.scenario == "work_unavailable":
                account["usageStatus"] = "unavailable"
                account["usage"] = None
                account.pop("usageFetchedAt", None)
                account.pop("usageAgeSeconds", None)
                account["lastGoodUsage"] = self._usage(74, 51)
                account["lastGoodFetchedAt"] = _iso(datetime.now(UTC) - timedelta(minutes=8))
                account["lastGoodAgeSeconds"] = 480.0
                account["usageError"] = "rate limited by upstream"
                account["usageRetryAt"] = _iso(datetime.now(UTC) + timedelta(minutes=4))

        if self.scenario == "high_usage":
            pct = 96.4 if alias == "work" else 88.1
            account["usage"] = {
                "fiveHour": _window(pct, timedelta(minutes=41)),
                "sevenDay": {
                    **_window(78.0, timedelta(days=1, hours=6)),
                    "expectedPct": 55.0,
                    "aheadOfPace": True,
                    "willLastToReset": False,
                },
                "spend": {
                    "used": 42.5,
                    "limit": 100.0,
                    "pct": 42.5,
                    "currency": "USD",
                    "resetsAt": _iso(datetime.now(UTC) + timedelta(days=9)),
                },
                "scoped": [],
            }
            account["usageStatus"] = "ok"
        return account

    def _payload(self) -> dict[str, Any]:
        if self.scenario == "no_accounts":
            return {"schemaVersion": 1, "activeAccountNumber": None, "accounts": []}
        accounts = [self._account("personal"), self._account("work")]
        active = next((a["number"] for a in accounts if a["active"]), None)
        return {
            "schemaVersion": 99 if self.scenario == "unknown_schema" else 1,
            "activeAccountNumber": active,
            "accounts": accounts,
        }

    # -- backend protocol ---------------------------------------------------

    def list_accounts(self) -> AccountList:
        self.calls.append("list")
        if self.fail_next_list is not None:
            error, self.fail_next_list = self.fail_next_list, None
            raise error
        return AccountList.parse(self._payload())

    def status(self) -> ActiveStatus:
        self.calls.append("status")
        if self.scenario == "no_accounts":
            return ActiveStatus.parse(
                {"schemaVersion": 1, "active": {"email": WORK_EMAIL, "managed": False}}
            )
        email = PERSONAL_EMAIL if self._active_alias == "personal" else WORK_EMAIL
        return ActiveStatus.parse(
            {"schemaVersion": 1, "active": {"email": email, "managed": True}}
        )

    def switch(self, alias: str, *, fallback_number: int | None = None) -> SwitchOutcome:
        self.calls.append(f"switch:{alias}")
        if self.switch_delay:
            time.sleep(self.switch_delay)
        if self.fail_next_switch is not None:
            error, self.fail_next_switch = self.fail_next_switch, None
            raise error
        if alias not in {"personal", "work"}:
            raise CswapError(CswapErrorKind.REPORTED, "No account matches that alias.")
        self._active_alias = alias
        email = PERSONAL_EMAIL if alias == "personal" else WORK_EMAIL
        return SwitchOutcome.parse(
            {
                "schemaVersion": 1,
                "active": {"email": email, "number": 1 if alias == "personal" else 2},
            }
        )

    def build_run_command(self, number: int) -> list[str]:
        self.calls.append(f"run:{number}")
        return ["<mock-cswap>", "run", str(number)]
