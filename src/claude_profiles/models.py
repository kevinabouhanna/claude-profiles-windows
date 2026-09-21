"""Typed models mirroring the ``cswap --json`` contract (schemaVersion 1).

Everything here is a plain frozen dataclass so the parsing layer can be unit
tested without Qt. Parsing is deliberately tolerant: every field that upstream
documents as conditional is optional here, and unknown enum members degrade to
``UsageStatus.UNKNOWN`` rather than raising. A future ``schemaVersion`` bump
surfaces as a warning banner, never as a crash.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

SCHEMA_VERSION = 1


def _parse_dt(value: Any) -> datetime | None:
    """Parse an ISO8601 timestamp (upstream emits UTC with a ``Z`` suffix)."""
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _parse_float(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _parse_bool(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


class UsageStatus(Enum):
    """The ``usageStatus`` enum, plus UNKNOWN for forward compatibility."""

    OK = "ok"
    TOKEN_EXPIRED = "token_expired"
    API_KEY = "api_key"
    KEYCHAIN_UNAVAILABLE = "keychain_unavailable"
    RELOGIN_REQUIRED = "relogin_required"
    FOREIGN_CREDENTIAL = "foreign_credential"
    NO_CREDENTIALS = "no_credentials"
    UNAVAILABLE = "unavailable"
    UNKNOWN = "unknown"

    @classmethod
    def parse(cls, value: Any) -> UsageStatus:
        if isinstance(value, str):
            try:
                return cls(value)
            except ValueError:
                return cls.UNKNOWN
        return cls.UNKNOWN

    @property
    def needs_reauth(self) -> bool:
        """Does this state require the user to log in again with Claude Code?"""
        return self in {
            UsageStatus.TOKEN_EXPIRED,
            UsageStatus.RELOGIN_REQUIRED,
            UsageStatus.NO_CREDENTIALS,
        }

    @property
    def is_healthy(self) -> bool:
        return self is UsageStatus.OK

    @property
    def label(self) -> str:
        return _STATUS_LABELS.get(self, "Unknown state")


_STATUS_LABELS: dict[UsageStatus, str] = {
    UsageStatus.OK: "Healthy",
    UsageStatus.TOKEN_EXPIRED: "Re-authentication required",
    UsageStatus.RELOGIN_REQUIRED: "Re-authentication required",
    UsageStatus.NO_CREDENTIALS: "Re-authentication required",
    UsageStatus.API_KEY: "API key account",
    UsageStatus.KEYCHAIN_UNAVAILABLE: "Credential store unavailable",
    UsageStatus.FOREIGN_CREDENTIAL: "Credentials changed outside claude-swap",
    UsageStatus.UNAVAILABLE: "Usage temporarily unavailable",
    UsageStatus.UNKNOWN: "Unknown state",
}


@dataclass(frozen=True)
class UsageWindow:
    """One quota window: the 5-hour, the 7-day, or a per-model ``scoped`` entry."""

    pct: float
    resets_at: datetime | None = None
    countdown: str | None = None
    clock: str | None = None
    name: str | None = None
    expected_pct: float | None = None
    ahead_of_pace: bool | None = None
    projected_exhaustion_at: datetime | None = None
    will_last_to_reset: bool | None = None

    @classmethod
    def parse(cls, data: Any) -> UsageWindow | None:
        if not isinstance(data, dict):
            return None
        pct = _parse_float(data.get("pct"))
        if pct is None:
            return None
        return cls(
            pct=pct,
            resets_at=_parse_dt(data.get("resetsAt")),
            countdown=data.get("countdown") or None,
            clock=data.get("clock") or None,
            name=data.get("name") or None,
            expected_pct=_parse_float(data.get("expectedPct")),
            ahead_of_pace=_parse_bool(data.get("aheadOfPace")),
            projected_exhaustion_at=_parse_dt(data.get("projectedExhaustionAt")),
            will_last_to_reset=_parse_bool(data.get("willLastToReset")),
        )


@dataclass(frozen=True)
class Spend:
    """The optional ``spend`` block (extra-usage dollars)."""

    used: float
    limit: float | None = None
    pct: float | None = None
    currency: str = "USD"
    resets_at: datetime | None = None
    countdown: str | None = None

    @classmethod
    def parse(cls, data: Any) -> Spend | None:
        if not isinstance(data, dict):
            return None
        used = _parse_float(data.get("used"))
        if used is None:
            return None
        return cls(
            used=used,
            limit=_parse_float(data.get("limit")),
            pct=_parse_float(data.get("pct")),
            currency=data.get("currency") or "USD",
            resets_at=_parse_dt(data.get("resetsAt")),
            countdown=data.get("countdown") or None,
        )


@dataclass(frozen=True)
class Usage:
    five_hour: UsageWindow | None = None
    seven_day: UsageWindow | None = None
    spend: Spend | None = None
    scoped: tuple[UsageWindow, ...] = ()

    @classmethod
    def parse(cls, data: Any) -> Usage | None:
        if not isinstance(data, dict):
            return None
        raw_scoped = data.get("scoped")
        scoped: list[UsageWindow] = []
        if isinstance(raw_scoped, list):
            for entry in raw_scoped:
                window = UsageWindow.parse(entry)
                if window is not None:
                    scoped.append(window)
        return cls(
            five_hour=UsageWindow.parse(data.get("fiveHour")),
            seven_day=UsageWindow.parse(data.get("sevenDay")),
            spend=Spend.parse(data.get("spend")),
            scoped=tuple(scoped),
        )


@dataclass(frozen=True)
class Account:
    """One row of ``cswap list --json``."""

    email: str
    number: int | None = None
    alias: str | None = None
    organization_name: str | None = None
    is_organization: bool = False
    active: bool = False
    disabled: bool = False
    usage_status: UsageStatus = UsageStatus.UNKNOWN
    usage: Usage | None = None
    usage_fetched_at: datetime | None = None
    usage_age_seconds: float | None = None
    last_good_usage: Usage | None = None
    last_good_fetched_at: datetime | None = None
    last_good_age_seconds: float | None = None
    usage_error: str | None = None
    usage_retry_at: datetime | None = None
    login_expires_at: datetime | None = None

    @classmethod
    def parse(cls, data: Any) -> Account | None:
        if not isinstance(data, dict):
            return None
        email = data.get("email")
        if not isinstance(email, str) or not email:
            return None
        number = data.get("number")
        return cls(
            email=email,
            number=number if isinstance(number, int) else None,
            alias=data.get("alias") or None,
            organization_name=data.get("organizationName") or None,
            is_organization=bool(data.get("isOrganization")),
            active=bool(data.get("active")),
            disabled=bool(data.get("disabled")),
            usage_status=UsageStatus.parse(data.get("usageStatus")),
            usage=Usage.parse(data.get("usage")),
            usage_fetched_at=_parse_dt(data.get("usageFetchedAt")),
            usage_age_seconds=_parse_float(data.get("usageAgeSeconds")),
            last_good_usage=Usage.parse(data.get("lastGoodUsage")),
            last_good_fetched_at=_parse_dt(data.get("lastGoodFetchedAt")),
            last_good_age_seconds=_parse_float(data.get("lastGoodAgeSeconds")),
            usage_error=data.get("usageError") or None,
            usage_retry_at=_parse_dt(data.get("usageRetryAt")),
            login_expires_at=_parse_dt(data.get("loginExpiresAt")),
        )

    @property
    def effective_usage(self) -> Usage | None:
        """Live usage when available, otherwise the retained last-good reading.

        claude-swap already keeps the previous successful reading, so the app
        renders that rather than maintaining a second cache that could drift.
        """
        return self.usage if self.usage is not None else self.last_good_usage

    @property
    def is_stale(self) -> bool:
        """True when we are showing a retained reading instead of a live one."""
        return self.usage is None and self.last_good_usage is not None

    @property
    def effective_age_seconds(self) -> float | None:
        return self.usage_age_seconds if self.usage is not None else self.last_good_age_seconds

    @property
    def effective_fetched_at(self) -> datetime | None:
        return self.usage_fetched_at if self.usage is not None else self.last_good_fetched_at


@dataclass(frozen=True)
class AccountList:
    """The whole ``cswap list --json`` payload."""

    active_account_number: int | None = None
    accounts: tuple[Account, ...] = ()
    schema_version: int = SCHEMA_VERSION

    @classmethod
    def parse(cls, data: Any) -> AccountList:
        if not isinstance(data, dict):
            raise ValueError("expected a JSON object")
        raw_accounts = data.get("accounts")
        accounts: list[Account] = []
        if isinstance(raw_accounts, list):
            for entry in raw_accounts:
                account = Account.parse(entry)
                if account is not None:
                    accounts.append(account)
        active = data.get("activeAccountNumber")
        version = data.get("schemaVersion")
        return cls(
            active_account_number=active if isinstance(active, int) else None,
            accounts=tuple(accounts),
            schema_version=version if isinstance(version, int) else SCHEMA_VERSION,
        )

    @property
    def is_known_schema(self) -> bool:
        return self.schema_version == SCHEMA_VERSION

    def by_alias(self, alias: str) -> Account | None:
        for account in self.accounts:
            if account.alias == alias:
                return account
        return None

    def by_number(self, number: int) -> Account | None:
        for account in self.accounts:
            if account.number == number:
                return account
        return None

    @property
    def active_account(self) -> Account | None:
        for account in self.accounts:
            if account.active:
                return account
        if self.active_account_number is not None:
            return self.by_number(self.active_account_number)
        return None


@dataclass(frozen=True)
class ActiveStatus:
    """``cswap status --json`` -> ``{"active": {"email", "managed"}}``.

    This is a different shape from a ``list`` account row. ``managed`` is False
    when the current Claude Code login has not been registered with claude-swap
    at all.
    """

    email: str | None = None
    managed: bool = False
    schema_version: int = SCHEMA_VERSION

    @classmethod
    def parse(cls, data: Any) -> ActiveStatus:
        if not isinstance(data, dict):
            raise ValueError("expected a JSON object")
        active = data.get("active")
        if not isinstance(active, dict):
            active = {}
        version = data.get("schemaVersion")
        email = active.get("email")
        return cls(
            email=email if isinstance(email, str) and email else None,
            managed=bool(active.get("managed")),
            schema_version=version if isinstance(version, int) else SCHEMA_VERSION,
        )


@dataclass(frozen=True)
class SwitchOutcome:
    """Result of ``cswap switch <n> --json``.

    The exact success payload is not part of the documented contract, so this
    stays deliberately thin: it records that the command succeeded plus any
    identifying fields that happened to be present. Authoritative state always
    comes from the ``list`` refresh that follows every switch.
    """

    ok: bool = True
    active_email: str | None = None
    active_number: int | None = None
    message: str | None = None

    @classmethod
    def parse(cls, data: Any) -> SwitchOutcome:
        if not isinstance(data, dict):
            return cls(ok=True)
        active = data.get("active")
        if not isinstance(active, dict):
            active = data
        email = active.get("email")
        number = active.get("number")
        message = data.get("message")
        return cls(
            ok=True,
            active_email=email if isinstance(email, str) and email else None,
            active_number=number if isinstance(number, int) else None,
            message=message if isinstance(message, str) and message else None,
        )


@dataclass(frozen=True)
class Profile:
    """A user-visible profile. Deliberately holds no email address.

    The alias is the only account identifier stored in source or settings; the
    email shown in the UI is whatever ``cswap list --json`` reports at runtime.
    """

    key: str
    name: str
    alias: str
    color: str


@dataclass
class ProfileState:
    """What the UI renders for one profile."""

    profile: Profile
    account: Account | None = None
    error: str | None = None

    @property
    def is_registered(self) -> bool:
        """False when no cswap account carries this profile's alias."""
        return self.account is not None

    @property
    def is_active(self) -> bool:
        return self.account is not None and self.account.active

    @property
    def display_label(self) -> str:
        """``Personal - someone@example.com``, assembled at runtime."""
        if self.account is None:
            return f"{self.profile.name} — not set up"
        return f"{self.profile.name} — {self.account.email}"

    @property
    def needs_reauth(self) -> bool:
        return self.account is not None and self.account.usage_status.needs_reauth


BLUE = "#3b82f6"
ORANGE = "#f97316"

DEFAULT_PROFILES: tuple[Profile, ...] = (
    Profile(key="personal", name="Personal", alias="personal", color=BLUE),
    Profile(key="work", name="Work", alias="work", color=ORANGE),
)


@dataclass(frozen=True)
class ActivityEntry:
    """One safe, user-facing activity-log line."""

    timestamp: datetime
    message: str
    level: str = "info"
    extra: dict[str, Any] = field(default_factory=dict)
