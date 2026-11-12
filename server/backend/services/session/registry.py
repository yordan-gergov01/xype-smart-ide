"""The open streams of this process, so a revocation or a shutdown reaches them at once
(ADR-0013, ADR-0014). Per process: every replica hears every revocation.
"""

import asyncio
import uuid
from collections import defaultdict
from dataclasses import dataclass, field

import structlog

from backend.services.auth import RevocationSubscriber
from common_lib.auth import AccessClaims, Revocation

logger = structlog.get_logger(__name__)


@dataclass(eq=False)
class StreamTicket:
    """One open stream. `ended` is set, once, with the reason it must close."""

    user_id: uuid.UUID
    family_id: uuid.UUID
    ended: asyncio.Event = field(default_factory=asyncio.Event)
    reason: str | None = None

    def end(self, reason: str) -> None:
        if self.reason is None:
            self.reason = reason
            self.ended.set()


class StreamRegistry:
    def __init__(self) -> None:
        self._by_user: defaultdict[uuid.UUID, set[StreamTicket]] = defaultdict(set)
        self._by_family: defaultdict[uuid.UUID, set[StreamTicket]] = defaultdict(set)
        self._draining = False

    def open(self, claims: AccessClaims) -> StreamTicket:
        """Registered when the stream opens, before Hello: a revocation reaches it too."""
        ticket = StreamTicket(claims.principal.user_id, claims.family_id)
        self._by_user[ticket.user_id].add(ticket)
        self._by_family[ticket.family_id].add(ticket)
        if self._draining:
            ticket.end("shutdown")
        return ticket

    def close(self, ticket: StreamTicket) -> None:
        for index, key in ((self._by_user, ticket.user_id), (self._by_family, ticket.family_id)):
            tickets = index.get(key)
            if tickets is not None:
                tickets.discard(ticket)
                if not tickets:
                    del index[key]

    def revoke(self, revocation: Revocation) -> int:
        """Ends the streams of a revoked user or token family; returns how many."""
        index = self._by_user if revocation.kind == "user" else self._by_family
        tickets = list(index.get(revocation.subject_id, ()))
        for ticket in tickets:
            ticket.end("revoked")
        return len(tickets)

    def drain(self) -> int:
        """Ends every stream (shutdown) and every stream opened from now on."""
        self._draining = True
        tickets = {t for group in self._by_user.values() for t in group}
        for ticket in tickets:
            ticket.end("shutdown")
        logger.info("session.drained", streams=len(tickets))
        return len(tickets)

    def __len__(self) -> int:
        return sum(len(group) for group in self._by_user.values())


async def listen_for_revocations(
    subscriber: RevocationSubscriber, registry: StreamRegistry
) -> None:
    """Runs until cancelled; the subscriber reconnects on its own."""
    async for revocation in subscriber.revocations():
        closed = registry.revoke(revocation)
        logger.info(
            "session.revoked",
            kind=revocation.kind,
            subject_id=str(revocation.subject_id),
            streams=closed,
        )
