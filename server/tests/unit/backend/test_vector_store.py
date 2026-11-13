"""The in-memory vector store meets the `VectorStore` contract (ADR-0020); the pgvector
adapter runs the same checks in tests/integration/test_vector_store.py."""

import uuid

import pytest
from support.contracts import vectors as contract
from support.vectors import FakeVectorStore

from common_lib.errors import InvalidValueError
from common_lib.vectors import VectorRecord


@pytest.mark.parametrize("check", contract.CHECKS, ids=lambda c: c.__name__)
async def test_fake_meets_the_contract(check: contract.Check) -> None:
    await check(FakeVectorStore(), (uuid.uuid4(), uuid.uuid4()))


def test_a_record_refuses_a_bad_vector_and_hides_it_from_repr() -> None:
    good = contract.record(uuid.uuid4(), 1, contract.axis((0, 0.5)))
    for vector in ([1.0] * 1023, contract.axis(), contract.axis((5, float("inf")))):
        with pytest.raises(InvalidValueError):
            VectorRecord(good.chunk, vector, good.model)
    assert "0.5" not in repr(good)
