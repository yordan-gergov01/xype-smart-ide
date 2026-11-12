"""The protobuf generator writes the messages and their stubs, and only into a `gen`
folder (it replaces the folder it writes)."""

from pathlib import Path

import pytest

from devtools.gen_proto import PROTO, generate


def test_generates_every_message_module_with_its_stub(tmp_path: Path) -> None:
    out = tmp_path / "gen"
    sources = generate(PROTO, out)
    for source in sources:
        stem = source.relative_to(PROTO).with_suffix("")
        assert (out / f"{stem}_pb2.py").is_file()
        assert (out / f"{stem}_pb2.pyi").is_file()
    assert not list(out.rglob("*_pb2_grpc.py"))


def test_refuses_to_replace_a_folder_not_named_gen(tmp_path: Path) -> None:
    keep = tmp_path / "src"
    keep.mkdir()
    (keep / "mine.py").write_text("x = 1\n")
    with pytest.raises(SystemExit, match="must be named gen"):
        generate(PROTO, keep)
    assert (keep / "mine.py").exists()
