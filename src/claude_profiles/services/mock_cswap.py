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
SIDE_EMAIL = "side-project@example.invalid"

# The demo accounts, in slot order: (email, alias, organisation, is_org,
# baseline 5h %, baseline 7d %). Three rather than two, so demo mode and the
# screenshots show that the number of accounts is not fixed.
_CATALOG: dict[str, tuple[str, str, bool, float, float]] = {
    PERSONAL_EMAIL: ("personal", "Personal", False, 42, 63),
    WORK_EMAIL: ("work", "Example Org", True, 74, 51),
    SIDE_EMAIL: ("side-project", "Side Project", False, 12, 28),
}
_ORG_UUIDS = {
    PERSONAL_EMAIL: "3f2504e0-4f89-11d3-9a0c-0305e82c3301",
    WORK_EMAIL: "6ba7b810-9dad-11d1-80b4-00c04fd430c8",
    SIDE_EMAIL: "1b4e28ba-2fa1-11d2-883f-0016d3cca427",
}

SCENARIOS = (
    "healthy",
    "two_accounts",
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


SEVEN_DAY_RESET = timedelta(days=3, hours=4)
SEVEN_DAY_EXPECTED = round(100 * (1 - SEVEN_DAY_RESET / timedelta(days=7)), 1)


class MockCswapClient:
    """Implements the :class:`CswapBackend` protocol with synthetic data."""

    def __init__(self, scenario: str = "healthy") -> None:
        if scenario not in SCENARIOS:
            raise ValueError(f"unknown scenario: {scenario}")
        self.scenario = scenario
        self._started = time.monotonic()
        # Registered accounts as {number, email, alias}. "no_accounts" starts
        # empty so the setup flow can be exercised end to end in demo mode.
        self._accounts: list[dict[str, Any]] = []
        demo_accounts = list(_CATALOG.items())[: 2 if scenario == "two_accounts" else None]
        if scenario != "no_accounts":
            for number, (email, (alias, *_)) in enumerate(demo_accounts, start=1):
                self._accounts.append({"number": number, "email": email, "alias": alias})
        self._active_number: int | None = 1 if self._accounts else None
        # Who Claude Code is currently signed in as, for `add` to pick up.
        self._signed_in_email = WORK_EMAIL
        # Test hooks.
        self.calls: list[str] = []
        self.fail_next_list: CswapError | None = None
        self.fail_next_switch: CswapError | None = None
        self.fail_next_add: CswapError | None = None
        self.switch_delay = 0.0
        self._counter = itertools.count()

    def set_signed_in(self, email: str) -> None:
        """Simulate the user signing in to Claude Code as a different account."""
        self._signed_in_email = email

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
                **_window(seven, SEVEN_DAY_RESET),
                # What real claude-swap reports: the share of the week elapsed.
                "expectedPct": SEVEN_DAY_EXPECTED,
                "aheadOfPace": seven > SEVEN_DAY_EXPECTED,
                "projectedExhaustionAt": _iso(datetime.now(UTC) + timedelta(days=2, hours=9)),
                "willLastToReset": seven < 80,
            },
            # No per-model ("scoped") rows: claude-swap only reports those for
            # accounts with model-specific limits, which typical accounts lack,
            # and the demo should look like a real account.
        }
        return usage

    def _account(self, entry: dict[str, Any]) -> dict[str, Any]:
        email = entry["email"]
        alias = entry["alias"]
        _, org, is_org, base_five, base_seven = _CATALOG.get(
            email, (alias, "Example", False, 30, 40)
        )
        account: dict[str, Any] = {
            "number": entry["number"],
            "email": email,
            "alias": alias,
            "organizationName": org,
            "organizationUuid": _ORG_UUIDS.get(email, "00000000-0000-4000-8000-000000000000"),
            "isOrganization": is_org,
            "active": self._active_number == entry["number"],
            "usageStatus": "ok",
            "usage": self._usage(base_five, base_seven),
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
            pct = {"work": 96.4, "personal": 88.1}.get(alias or "", 91.7)
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
        accounts = [self._account(entry) for entry in self._accounts]
        return {
            "schemaVersion": 99 if self.scenario == "unknown_schema" else 1,
            "activeAccountNumber": self._active_number,
            "accounts": accounts,
        }

    def _find(self, target: str | int | None) -> dict[str, Any] | None:
        for entry in self._accounts:
            if target in (entry["number"], entry["alias"], entry["email"]):
                return entry
        return None

    # -- backend protocol ---------------------------------------------------

    def list_accounts(self) -> AccountList:
        self.calls.append("list")
        if self.fail_next_list is not None:
            error, self.fail_next_list = self.fail_next_list, None
            raise error
        return AccountList.parse(self._payload())

    def status(self) -> ActiveStatus:
        self.calls.append("status")
        active = self._find(self._active_number)
        if active is None:
            return ActiveStatus.parse(
                {
                    "schemaVersion": 1,
                    "active": {"email": self._signed_in_email, "managed": False},
                }
            )
        return ActiveStatus.parse(
            {"schemaVersion": 1, "active": {"email": active["email"], "managed": True}}
        )

    def switch(
        self, alias: str | None, *, fallback_number: int | None = None
    ) -> SwitchOutcome:
        self.calls.append(f"switch:{alias if alias is not None else fallback_number}")
        if self.switch_delay:
            time.sleep(self.switch_delay)
        if self.fail_next_switch is not None:
            error, self.fail_next_switch = self.fail_next_switch, None
            raise error
        entry = self._find(alias if alias is not None else fallback_number)
        if entry is None:
            raise CswapError(CswapErrorKind.REPORTED, "No account matches that alias.")
        self._active_number = entry["number"]
        return SwitchOutcome.parse(
            {
                "schemaVersion": 1,
                "active": {"email": entry["email"], "number": entry["number"]},
            }
        )

    def add_current_account(self, alias: str) -> str:
        self.calls.append(f"add:{alias}")
        if self.fail_next_add is not None:
            error, self.fail_next_add = self.fail_next_add, None
            raise error
        owner = self._find(alias)
        if owner is not None and owner["email"] != self._signed_in_email:
            raise CswapError(
                CswapErrorKind.REPORTED,
                f"An account is already registered as '{alias}'.",
            )
        existing = self._find(self._signed_in_email)
        if existing is not None:
            # cswap updates the slot of an address it already knows.
            existing["alias"] = alias
            return f"Updated the signed-in account as '{alias}'."
        number = max((e["number"] for e in self._accounts), default=0) + 1
        self._accounts.append({"number": number, "email": self._signed_in_email, "alias": alias})
        if self._active_number is None:
            self._active_number = number
        return f"Added the signed-in account as '{alias}'."

    def set_alias(self, number: int, alias: str) -> str:
        self.calls.append(f"alias:{number}:{alias}")
        entry = self._find(number)
        if entry is not None:
            entry["alias"] = alias
        return f"Account {number} is now aliased '{alias}'."

    def build_run_command(self, number: int) -> list[str]:
        self.calls.append(f"run:{number}")
        return ["<mock-cswap>", "run", str(number)]
