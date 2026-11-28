"""Phase 2 exit: the seeded sample repository indexed end to end, from the extension's sync
worker through the edge proxy, the gRPC stream, the broker and the indexer into pgvector;
one changed function syncs as a delta of exactly one chunk; and no canary of the sample
repository is anywhere on the server (spec `.claude/specs/phase-2/spec.md`, acceptance 1
and 2; runbook `docs/runbooks/e2e.md`).

Needs the running e2e stack and the built extension, so it is marked `e2e` and skipped
unless `XYPE_E2E_PROJECT` names the stack."""

import json
import re
import secrets
import shutil
import time
import uuid
from pathlib import Path

import pytest
from support.e2e_client import SyncClient
from support.e2e_stack import Stack
from support.privacy_scan import (
    CONTROL_VALUE,
    Canaries,
    canaries,
    plant_controls,
    queued_messages,
    scan,
)

pytestmark = pytest.mark.e2e

SAMPLE = Path(__file__).resolve().parents[1] / "fixtures" / "sample-repo"
EDITED = Path("app") / "zqv_orchard_ledger.py"
SETTLE_S = 300
# Words each place is known to hold: table names, an indexer log event, a span field
# and the key planted in both Redis stores.
CONTROLS = Canaries(
    words=frozenset(
        {b"merkle_nodes", b"index.chunks", b"indexer.chunk.done", b"Span #", CONTROL_VALUE.encode()}
    ),
    hashes=frozenset(),
)
POLL_S = 2


@pytest.fixture(scope="module")
def stack() -> Stack:
    found = Stack.from_environment()
    if found is None:
        pytest.skip("the e2e stack is not running (docs/runbooks/e2e.md)")
    return found


def test_sample_repo_end_to_end(stack: Stack, tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    shutil.copytree(SAMPLE, root)
    # A secret that matches a scrubber rule, made here so none is committed.
    secret = "ghp_" + "".join(
        secrets.choice("ABCDEFGHJKLMNPQRSTUVWXYZ0123456789") for _ in range(36)
    )
    (root / "app" / "zqv_settings.py").write_text(f'ZQV_TOKEN = "{secret}"\n', encoding="utf-8")
    # Ignored by the sample's `.gitignore`, so it is made here too; it must never be read.
    (root / "zqv_ignored_scratch.txt").write_text("zqvignoredcanary\n", encoding="utf-8")
    tracked = sum(1 for path in root.rglob("*") if path.is_file()) - 1
    words = canaries(root, extra=[secret])
    repository, credentials = _register(stack, tmp_path)

    with SyncClient(
        server_url=stack.server_url, credentials=credentials, repository=repository, root=root
    ) as client:
        stats = client.wait_for("indexed", timeout_s=120)["stats"]
        assert stats["files"] == tracked, "every file but the git-ignored one is walked"
        first = _settled(stack, repository)
        assert first["indexed"] == _chunk_count(stack, repository) > 0
        assert first.get("rejected", 0) == 0

        since = stack.sql("SELECT now()")[0][0]
        source = (root / EDITED).read_text(encoding="utf-8")
        (root / EDITED).write_text(source.replace("zqvlabel-original", "zqvlabel-changed1"))
        client.send("changed")
        client.wait_for("indexed", timeout_s=60)
        second = _settled(stack, repository, changed_since=since)
    credentials.unlink()  # the client secret is not left in pytest's temporary directory

    assert second == first, "a one-line edit must not change the number of leaves"
    assert _changed_chunks(stack, repository, since) == 1
    words |= ["zqvlabel-changed1"]
    assert scan(stack, words) == []
    # Positive control: the scan really reads each kind of place.
    plant_controls(stack)
    places = {finding.place for finding in scan(stack, CONTROLS)}
    read = {"postgres dump", "postgres disk", "rabbitmq disk", "indexer log"}
    assert read | {"redis keys", "keystore keys"} <= places
    assert "otel-collector log" in places, "no span reached the collector"
    assert queued_messages(stack) == {}


def _register(stack: Stack, tmp_path: Path) -> tuple[str, Path]:
    """A team, a client and a repository; the credentials go to a file only."""
    team = f"e2e-{uuid.uuid4().hex[:8]}"
    stack.admin("create-team", team)
    # Never run this test with `--showlocals`: the client secret is a local here.
    shown = stack.admin(
        "create-client", "--team", team, "--email", f"{team}@example.com", "--name", "E2E"
    )
    client_id = _field(shown, "client_id")
    client_secret = _field(shown, "client_secret")
    registered = stack.admin(
        "register-repo",
        "--team",
        team,
        "--name",
        "sample",
        "--remote-url",
        f"https://git.example.com/{team}.git",
    )
    credentials = tmp_path / "credentials.json"
    credentials.write_text(json.dumps({"clientId": client_id, "clientSecret": client_secret}))
    return _field(registered, "repository_id"), credentials


def _field(output: str, name: str) -> str:
    match = re.search(rf"^{name}:\s+(\S+)$", output, re.MULTILINE)
    assert match is not None, f"admin_cli printed no {name}"
    return match.group(1)


def _leaf_states(stack: Stack, repository: str) -> dict[str, int]:
    rows = stack.sql(
        "SELECT index_state, count(*) FROM merkle_nodes"
        " WHERE is_leaf AND repository_id = :'repo' GROUP BY 1",
        repo=repository,
    )
    return {state: int(count) for state, count in rows}


def _settled(stack: Stack, repository: str, changed_since: str | None = None) -> dict[str, int]:
    """Leaf states once none is pending (and, after an edit, once a chunk has changed)."""
    deadline = time.monotonic() + SETTLE_S
    while time.monotonic() < deadline:
        states = _leaf_states(stack, repository)
        changed = changed_since is None or _changed_chunks(stack, repository, changed_since) > 0
        if states and states.get("pending", 0) == 0 and changed:
            return states
        time.sleep(POLL_S)
    pytest.fail(f"the index did not settle within {SETTLE_S} s: {_leaf_states(stack, repository)}")


def _chunk_count(stack: Stack, repository: str) -> int:
    query = "SELECT count(*) FROM code_chunks WHERE repository_id = :'repo'"
    return int(stack.sql(query, repo=repository)[0][0])


def _changed_chunks(stack: Stack, repository: str, since: str) -> int:
    query = (
        "SELECT count(*) FROM code_chunks"
        " WHERE repository_id = :'repo' AND updated_at > :'since'::timestamptz"
    )
    return int(stack.sql(query, repo=repository, since=since)[0][0])
