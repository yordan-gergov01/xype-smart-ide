"""The `Session` stream's own timers (ADR-0013), on a fixed clock: no sleeping."""

from datetime import UTC, datetime, timedelta

import pytest

from backend.services.session import Due, StreamPolicy, StreamTimers

OPENED = datetime(2026, 11, 11, 9, 0, tzinfo=UTC)
POLICY = StreamPolicy(
    heartbeat_interval_s=20,
    idle_timeout_s=60,
    hello_timeout_s=10,
    refresh_lead_s=60,
    max_repositories=64,
    requests_per_minute=200,
)


def timers(expires_in: timedelta = timedelta(minutes=15)) -> StreamTimers:
    return StreamTimers(POLICY, opened_at=OPENED, token_expires_at=OPENED + expires_in)


def at(seconds: float) -> datetime:
    return OPENED + timedelta(seconds=seconds)


def test_quiet_until_the_hello_deadline() -> None:
    clock = timers()
    assert clock.due(at(9)) is None
    assert clock.next_check(at(9)) == 1
    assert clock.due(at(10)) is Due.NO_HELLO


def test_heartbeats_keep_the_stream_open() -> None:
    clock = timers()
    clock.seen(at(1), hello=True)
    for second in range(20, 600, 20):
        clock.seen(at(second))
        assert clock.due(at(second)) is None
    assert clock.due(at(580 + 59)) is None
    assert clock.due(at(580 + 60)) is Due.IDLE


def test_refresh_notice_once_then_close_at_expiry() -> None:
    clock = timers()
    clock.seen(at(1), hello=True)
    for second in range(0, 840, 30):
        clock.seen(at(second))
    assert clock.next_check(at(810)) == pytest.approx(30)
    assert clock.due(at(840)) is Due.REFRESH
    clock.seen(at(840))
    assert clock.due(at(841)) is None
    assert clock.next_check(at(870)) == pytest.approx(30)
    clock.seen(at(899))
    assert clock.due(at(900)) is Due.EXPIRED


def test_a_close_wins_over_the_refresh_notice() -> None:
    clock = timers(expires_in=timedelta(seconds=5))
    assert clock.due(at(5)) is Due.EXPIRED


def test_a_token_close_to_expiry_gets_the_notice_at_once() -> None:
    clock = timers(expires_in=timedelta(seconds=30))
    assert clock.due(at(0)) is Due.REFRESH
    assert clock.due(at(1)) is None
