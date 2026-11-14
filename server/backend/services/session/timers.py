"""When a stream must act on its own: the timers of one `Session` stream (ADR-0013).

A pure state machine over the injected clock, so the transport only asks "what now, and
when next?" and the rules are tested without sleeping.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum


class Due(StrEnum):
    """What the stream has to do now."""

    REFRESH = "refresh"  # send TokenRefreshRequired (once)
    NO_HELLO = "no_hello"  # the client never said Hello
    IDLE = "idle"  # no message, not even a heartbeat, for too long
    EXPIRED = "expired"  # the access token the stream was opened with expired
    POLL = "poll"  # ask for the roots again (ADR-0018's 10-minute poll), once per interval


@dataclass(frozen=True, slots=True)
class StreamPolicy:
    heartbeat_interval_s: int
    # Closed when nothing arrives for this long (a few missed heartbeats).
    idle_timeout_s: int
    hello_timeout_s: int
    # TokenRefreshRequired goes out this long before the token expires.
    refresh_lead_s: int
    max_repositories: int
    # Announced in HelloAck: the per-user limit stream messages count against.
    requests_per_minute: int
    # After Hello, the server asks for every repository's root this often (ADR-0018).
    merkle_poll_interval_s: int = 600


class StreamTimers:
    def __init__(self, policy: StreamPolicy, *, opened_at: datetime, token_expires_at: datetime):
        self._policy = policy
        self._opened_at = opened_at
        self._expires_at = token_expires_at
        self._last_seen = opened_at
        self._hello = False
        self._refresh_sent = False
        self._polled_at = opened_at

    @property
    def token_expires_at(self) -> datetime:
        return self._expires_at

    def seen(self, now: datetime, *, hello: bool = False) -> None:
        """Any message from the client keeps the stream alive. The poll interval starts
        at Hello, which already compares the roots."""
        self._last_seen = now
        if hello and not self._hello:
            self._polled_at = now
        self._hello = self._hello or hello

    def due(self, now: datetime) -> Due | None:
        """The most urgent thing to do now; a close wins over the refresh notice."""
        if now >= self._expires_at:
            return Due.EXPIRED
        if not self._hello and now >= self._hello_deadline:
            return Due.NO_HELLO
        if now >= self._idle_deadline:
            return Due.IDLE
        if not self._refresh_sent and now >= self._refresh_at:
            self._refresh_sent = True
            return Due.REFRESH
        if self._hello and now >= self._poll_at:
            self._polled_at = now
            return Due.POLL
        return None

    def next_check(self, now: datetime) -> float:
        """Seconds until `due` may answer something new (0 when it already does)."""
        deadlines = [self._expires_at, self._idle_deadline]
        if not self._hello:
            deadlines.append(self._hello_deadline)
        if not self._refresh_sent:
            deadlines.append(self._refresh_at)
        if self._hello:
            deadlines.append(self._poll_at)
        return max(0.0, (min(deadlines) - now).total_seconds())

    @property
    def _hello_deadline(self) -> datetime:
        return self._opened_at + timedelta(seconds=self._policy.hello_timeout_s)

    @property
    def _idle_deadline(self) -> datetime:
        return self._last_seen + timedelta(seconds=self._policy.idle_timeout_s)

    @property
    def _poll_at(self) -> datetime:
        return self._polled_at + timedelta(seconds=self._policy.merkle_poll_interval_s)

    @property
    def _refresh_at(self) -> datetime:
        return self._expires_at - timedelta(seconds=self._policy.refresh_lead_s)
