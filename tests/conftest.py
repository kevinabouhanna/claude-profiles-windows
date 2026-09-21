"""Shared test fixtures."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from claude_profiles.services.cswap_client import CswapClient
from claude_profiles.services.settings_service import SettingsService


@dataclass
class FakeCompleted:
    """Stands in for ``subprocess.CompletedProcess``."""

    stdout: str = ""
    stderr: str = ""
    returncode: int = 0


class FakeRunner:
    """Records argv and replays scripted responses in place of subprocess.run."""

    def __init__(self, *responses: Any) -> None:
        self._responses = list(responses)
        self.calls: list[list[str]] = []
        self.kwargs: list[dict[str, Any]] = []

    def __call__(self, argv: list[str], **kwargs: Any) -> FakeCompleted:
        self.calls.append(list(argv))
        self.kwargs.append(kwargs)
        if not self._responses:
            return FakeCompleted(stdout="{}")
        response = self._responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        if isinstance(response, FakeCompleted):
            return response
        return FakeCompleted(stdout=str(response))

    @property
    def last_args(self) -> list[str]:
        return self.calls[-1] if self.calls else []


@pytest.fixture
def fake_runner() -> type[FakeRunner]:
    return FakeRunner


@pytest.fixture
def make_client():
    """Build a CswapClient backed by scripted responses."""

    def _make(*responses: Any, executable: str = "cswap.exe") -> tuple[CswapClient, FakeRunner]:
        runner = FakeRunner(*responses)
        client = CswapClient(executable=executable, runner=runner)
        return client, runner

    return _make


@pytest.fixture
def timeout_error() -> subprocess.TimeoutExpired:
    return subprocess.TimeoutExpired(cmd="cswap", timeout=20)


@pytest.fixture
def settings_service(tmp_path: Path) -> SettingsService:
    return SettingsService(data_dir=tmp_path / "ClaudeProfiles")
