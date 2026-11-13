"""Repository registration (ADR-0016, plan §3a): a random key wrapped under the KEK with
an AAD that binds the row, credentials stripped from the remote URL, and errors that
never repeat the URL. The fake store meets the shared contract."""

import re
import uuid

import pytest
import structlog
from support.auth import FakeUserStore
from support.backend import make_settings
from support.contracts import repositories as contract
from support.repositories import FakeRepositoryStore

from backend.core.auth import kek_ring
from backend.services.repositories import RepositoryAdmin, UnknownTeamError, without_credentials
from common_lib.clock import SystemClock
from common_lib.errors import InvalidValueError
from common_lib.remote_urls import REMOTE_URL_FORM, SCP_FORM
from common_lib.repositories import DuplicateRepositoryError
from common_lib.security import DecryptionError, KekPurpose, repository_key_aad, unwrap_key

RING = kek_ring(make_settings())


@pytest.fixture
async def admin() -> tuple[RepositoryAdmin, FakeRepositoryStore]:
    users, store = FakeUserStore(), FakeRepositoryStore()
    await users.create_team("Web")
    return RepositoryAdmin(store, users, kek=RING.current, clock=SystemClock()), store


async def test_registration_wraps_a_fresh_key_bound_to_the_row(
    admin: tuple[RepositoryAdmin, FakeRepositoryStore],
) -> None:
    service, store = admin
    first = await service.register(team="Web", name="app", remote_url=None)
    second = await service.register(team="Web", name="lib", is_template=True)
    row = store.repositories[first.repository_id]
    assert (row.hmac_key_id, row.name, row.is_template) == (first.hmac_key_id, "app", False)
    assert store.repositories[second.repository_id].is_template
    aad = repository_key_aad(str(row.repository_id), row.hmac_key_id, RING.current.kek_id)
    key = unwrap_key(RING, KekPurpose.REPO_KEYS, row.wrapped_key, aad).reveal()
    assert len(key) == 32
    other = store.repositories[second.repository_id]
    other_aad = repository_key_aad(str(other.repository_id), other.hmac_key_id, RING.current.kek_id)
    assert unwrap_key(RING, KekPurpose.REPO_KEYS, other.wrapped_key, other_aad).reveal() != key
    # The row cannot be moved to another repository or key id.
    with pytest.raises(DecryptionError):
        unwrap_key(RING, KekPurpose.REPO_KEYS, row.wrapped_key, other_aad)


async def test_refuses_an_unknown_team_a_bad_name_and_a_duplicate(
    admin: tuple[RepositoryAdmin, FakeRepositoryStore],
) -> None:
    service, store = admin
    with pytest.raises(UnknownTeamError):
        await service.register(team="Nobody", name="app")
    for name in ("", "   ", "x" * 201):
        with pytest.raises(InvalidValueError):
            await service.register(team="Web", name=name)
    await service.register(team="Web", name="app")
    with pytest.raises(DuplicateRepositoryError):
        await service.register(team="Web", name=" app ")
    assert len(store.repositories) == 1


async def test_the_log_names_ids_only(admin: tuple[RepositoryAdmin, FakeRepositoryStore]) -> None:
    service, _ = admin
    url = "https://user:tok3n@git.example.test/web/app.git"
    with structlog.testing.capture_logs() as logs:
        registered = await service.register(team="Web", name="secret-name", remote_url=url)
    assert logs[-1]["repository_id"] == str(registered.repository_id)
    for text in ("secret-name", "tok3n", "git.example.test"):
        assert text not in repr(logs)


# What registration stores; every stored form also passes the database CHECK.
STORED = [
    ("https://u:tok@host.test/r.git", "https://host.test/r.git"),
    ("https://tok@Host.Test/r.git", "https://host.test/r.git"),
    ("ssh://git@host.test:22/r.git", "ssh://host.test:22/r.git"),
    ("git://host.test/r.git", "git://host.test/r.git"),
    ("https://host.test/r.git?private_token=x#frag", "https://host.test/r.git"),
    ("https://host.test", "https://host.test"),
    ("  https://host.test/r.git  ", "https://host.test/r.git"),
    ("git@host.test:org/r.git", "git@host.test:org/r.git"),
    ("ghp_tok@github.test:org/r.git", "github.test:org/r.git"),
    ("git:tok3n@host.test:org/r.git", "host.test:org/r.git"),
    ("git@host.test:org/r.git?private_token=x", "git@host.test:org/r.git"),
    ("host.test:org/r.git", "host.test:org/r.git"),
]

# Refused, each without repeating the URL: passwords that a naive split would keep,
# local paths, other schemes, non-ASCII and oversized values.
REFUSED = [
    "https://u:p/ss@host.test/r",
    "https://user:pa#ss@host.test/r",
    "https://user:pa?ss@host.test/r",
    "https://user:1234/ss@host.test/r",
    "https://host.test:notaport/r",
    "https://deploy:12345#rest@host.test/r",
    "https://deploy:12345?rest@host.test/r",
    "https://deploy:0#x@host.test",
    "https://intranet/r.git",
    "https://host.test/a@b/r.git",
    "tok3n@host.test/r.git",
    "//tok3n@host.test/r.git",
    r"C:\Users\someone\src\r",
    "C:/Users/someone/src/r",
    "../local/r.git",
    "/srv/git/r.git",
    "file:///srv/git/r.git",
    "http://host.test/r.git",
    "https://",
    "://tok3n@host.test",
    "https://host.test/a b",
    "https://host.test/r\u200b.git",  # a zero-width space
    "https://[tok3n/r.git",
    "https://host.test/" + "r" * 2048,
    "",
]


@pytest.mark.parametrize(("url", "stored"), STORED)
def test_credentials_are_stripped(url: str, stored: str) -> None:
    assert without_credentials(url) == stored
    assert re.fullmatch(REMOTE_URL_FORM, stored) or re.fullmatch(SCP_FORM, stored)


@pytest.mark.parametrize("url", REFUSED)
def test_bad_urls_are_refused_without_repeating_them(url: str) -> None:
    with pytest.raises(InvalidValueError) as caught:
        without_credentials(url)
    message = str(caught.value)
    assert "tok3n" not in message
    assert "someone" not in message
    assert url.strip() == "" or url not in message


@pytest.mark.parametrize("url", [u for u in REFUSED if len(u) <= 2048])
def test_the_check_refuses_what_registration_refuses(url: str) -> None:
    """The CHECK patterns are a second line: none of the refused forms matches them."""
    assert re.fullmatch(REMOTE_URL_FORM, url) is None
    assert re.fullmatch(SCP_FORM, url) is None


TEAMS = (uuid.uuid4(), uuid.uuid4())


async def test_fake_keys_round_trip_for_the_team_only() -> None:
    await contract.check_keys_round_trip_for_the_team_only(FakeRepositoryStore(), TEAMS)


async def test_fake_name_is_unique_per_team() -> None:
    await contract.check_a_name_is_unique_per_team(FakeRepositoryStore(), TEAMS)


async def test_fake_leaves_out_a_retired_key() -> None:
    store = FakeRepositoryStore()

    async def retire(repository_id: uuid.UUID) -> None:
        store.retired.add(repository_id)

    await contract.check_a_retired_key_is_not_issued(store, retire, TEAMS[0])
