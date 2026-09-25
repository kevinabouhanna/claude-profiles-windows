"""Tests for the per-profile setup wizard.

The important one is :func:`test_status_from_worker_thread_reaches_the_ui`.
Results are produced on a worker thread, and the obvious way to hand them back
- ``QTimer.singleShot(0, callback)`` - does not work from a non-GUI thread: the
timer is created in the calling thread, which has no event loop, so the
callback never runs and the dialog sits on "Checking..." forever. Nothing
raises, so only a behavioural test catches it.
"""

from __future__ import annotations

import threading
import time

import pytest

from claude_profiles.models import DEFAULT_PROFILES, ActiveStatus
from claude_profiles.widgets.setup_dialog import SetupDialog

PERSONAL = DEFAULT_PROFILES[0]
WORK = DEFAULT_PROFILES[1]

SIGNED_IN = ActiveStatus(email="someone@example.invalid", managed=False)
MANAGED = ActiveStatus(email="someone@example.invalid", managed=True)
OTHER = ActiveStatus(email="other@example.invalid", managed=False)


@pytest.fixture
def dialog(qtbot):
    def _make(reader=lambda: SIGNED_IN, profile=PERSONAL):
        d = SetupDialog(profile, reader)
        qtbot.addWidget(d)
        return d

    return _make


# --- cross-thread delivery ------------------------------------------------


def test_status_from_worker_thread_reaches_the_ui(dialog, qtbot):
    """The poll runs off the GUI thread; its result must still land."""
    calls: list[str] = []

    def reader() -> ActiveStatus:
        calls.append(threading.current_thread().name)
        time.sleep(0.02)  # force the result to arrive after _poll() returns
        return SIGNED_IN

    d = dialog(reader)
    d.start()

    qtbot.waitUntil(lambda: d._email_label.text() == SIGNED_IN.email, timeout=3000)
    assert calls and calls[0] != "MainThread"
    assert d._register_button.isEnabled()


def test_a_failing_reader_does_not_break_the_dialog(dialog, qtbot):
    def reader():
        raise RuntimeError("cswap exploded")

    d = dialog(reader)
    d.start()

    qtbot.waitUntil(lambda: d._email_label.text() == "Not signed in", timeout=3000)
    assert d._register_button.isEnabled() is False


def test_polls_do_not_overlap(dialog, qtbot):
    in_flight = {"now": 0, "max": 0}
    lock = threading.Lock()

    def reader() -> ActiveStatus:
        with lock:
            in_flight["now"] += 1
            in_flight["max"] = max(in_flight["max"], in_flight["now"])
        time.sleep(0.05)
        with lock:
            in_flight["now"] -= 1
        return SIGNED_IN

    d = dialog(reader)
    for _ in range(10):
        d._poll()

    qtbot.waitUntil(lambda: d._email_label.text() == SIGNED_IN.email, timeout=3000)
    assert in_flight["max"] == 1


# --- state transitions ----------------------------------------------------


def test_unsigned_state_blocks_saving(dialog):
    d = dialog()
    d.update_status(None)
    assert d._email_label.text() == "Not signed in"
    assert d._register_button.isEnabled() is False


def test_new_account_is_offered_for_saving(dialog):
    d = dialog()
    d.update_status(SIGNED_IN)
    assert d._register_button.isEnabled()
    assert d._email_badge._label.text() == "New account"


def test_already_managed_account_is_labelled(dialog):
    d = dialog()
    d.update_status(MANAGED)
    assert d._email_badge._label.text() == "Already in claude-swap"
    assert d._register_button.isEnabled()


def test_sign_in_change_is_detected_and_stops_polling(dialog):
    d = dialog()
    d.update_status(SIGNED_IN)
    d.begin_waiting()
    assert d._polling is True

    d.update_status(OTHER)  # the user signed in as someone else

    assert d._polling is False
    assert d._email_label.text() == OTHER.email
    assert "Signed in" in d._waiting_label.text()


def test_same_account_after_waiting_keeps_watching(dialog):
    """Sign-in has not happened yet, so the dialog must keep looking."""
    d = dialog()
    d.update_status(SIGNED_IN)
    d.begin_waiting()

    d.update_status(SIGNED_IN)

    assert d._polling is True


def test_busy_disables_both_actions(dialog):
    d = dialog()
    d.update_status(SIGNED_IN)

    d.set_busy(True)
    assert d._register_button.isEnabled() is False
    assert d._signin_button.isEnabled() is False

    d.set_busy(False)
    assert d._register_button.isEnabled() is True
    assert d._signin_button.isEnabled() is True


def test_success_result_finishes_the_dialog(dialog):
    d = dialog()
    d.update_status(SIGNED_IN)
    d.set_result("Registered someone@example.invalid as Personal.", ok=True)

    assert d._close_button.text() == "Done"
    assert d._register_button.isEnabled() is False
    assert d._polling is False


def test_failure_result_leaves_the_dialog_usable(dialog):
    d = dialog()
    d.update_status(SIGNED_IN)
    d.set_result("No Claude Code credentials were found.", ok=False)

    assert d._close_button.text() == "Close"
    assert "No Claude Code credentials" in d._result.text()


def test_closing_stops_the_poll_timer(dialog):
    d = dialog()
    d.update_status(SIGNED_IN)
    d.begin_waiting()
    d.close()
    assert d._polling is False
    assert d._poll_timer.isActive() is False


def test_dialog_is_titled_for_its_profile(dialog):
    assert dialog(profile=WORK).windowTitle() == "Set up Work"
    assert dialog(profile=WORK).profile.key == "work"
