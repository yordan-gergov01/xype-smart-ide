from datetime import UTC, datetime, timedelta

from support.clock import FakeClock

from common_lib.clock import Clock, SystemClock

_system: Clock = SystemClock()
_fake: Clock = FakeClock(datetime(2026, 1, 1, tzinfo=UTC))


def test_system_clock_is_utc_and_does_not_go_back() -> None:
    clock = SystemClock()
    first, second = clock.now(), clock.now()
    assert first.tzinfo is UTC
    assert second >= first


def test_fake_clock_moves_only_when_told() -> None:
    clock = FakeClock(datetime(2026, 1, 1, 12, tzinfo=UTC))
    assert clock.now() == datetime(2026, 1, 1, 12, tzinfo=UTC)
    clock.advance(timedelta(hours=2))
    assert clock.now() == datetime(2026, 1, 1, 14, tzinfo=UTC)
