"""The in-memory Merkle index meets the `MerkleIndex` contract (ADR-0018), and the value
types refuse what the tree can never hold. The Postgres adapter runs the same checks in
tests/integration/test_merkle_index.py."""

import uuid

import pytest
from support.contracts import merkle as contract
from support.merkle import FakeMerkleIndex

from common_lib.errors import InvalidValueError
from common_lib.merkle import MAX_NODES_PER_APPLY, MerkleNodeUpdate, MerkleNodeView, MerkleUpdate

HEX = contract.hexid


@pytest.mark.parametrize("check", contract.CHECKS, ids=lambda c: c.__name__)
async def test_fake_meets_the_contract(check: contract.Check) -> None:
    index, repositories = FakeMerkleIndex(), (uuid.uuid4(), uuid.uuid4())
    for repository_id in repositories:
        index.register(repository_id)
    await check(index, repositories)


@pytest.mark.parametrize(
    "build",
    [
        lambda: MerkleNodeView("src/app.py", HEX(1), is_leaf=False),
        lambda: MerkleNodeView(HEX(1), "A" * 64, is_leaf=True),
        lambda: MerkleNodeUpdate(HEX(1), HEX(1), HEX(2), is_leaf=False),
        lambda: MerkleNodeUpdate(HEX(1), "dir/", HEX(2), is_leaf=False),
        lambda: MerkleNodeUpdate(HEX(1), None, HEX(2), is_leaf=True),
        lambda: MerkleNodeUpdate(HEX(1), None, HEX(2), is_leaf=False, lines=(1, 2)),
        lambda: MerkleNodeUpdate(HEX(1), None, HEX(2), is_leaf=True, lines=(5, 4)),
        lambda: MerkleNodeUpdate(HEX(1), None, HEX(2), is_leaf=True, lines=(0, 4)),
        lambda: MerkleUpdate("root", ()),
        lambda: MerkleUpdate(HEX(9), (), deleted=("src",)),
        lambda: MerkleUpdate(
            HEX(9), (MerkleNodeUpdate(HEX(1), None, HEX(2), is_leaf=False),), deleted=(HEX(1),)
        ),
    ],
    ids=[
        "plain node id",
        "uppercase hash",
        "own parent",
        "plain parent",
        "leaf without lines",
        "inner node with lines",
        "reversed lines",
        "line zero",
        "plain root",
        "plain deleted id",
        "stored and deleted",
    ],
)
def test_the_tree_refuses_what_it_can_never_hold(build: object) -> None:
    with pytest.raises(InvalidValueError):
        build()  # type: ignore[operator]


def test_an_apply_is_capped() -> None:
    too_many = [HEX(n) for n in range(MAX_NODES_PER_APPLY + 1)]
    with pytest.raises(InvalidValueError):
        MerkleUpdate(HEX(9), (), deleted=too_many)
