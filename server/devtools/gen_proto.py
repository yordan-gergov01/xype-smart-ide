"""Generates the Python protobuf modules and their .pyi stubs from proto/ (ADR-0013).

    python tools/server_tool.py python -m devtools.gen_proto

One place for the protoc arguments: local development, pre-commit, CI and the image's
codegen stage all run this. The output (`server/gen/xype/v1/*_pb2.py[i]`) is never
committed. No gRPC service stubs are generated: the backend registers its one method
with a generic handler (`backend.grpc`), which keeps the code under mypy --strict.
"""

import argparse
import shutil
import sys
from importlib import resources
from pathlib import Path

from grpc_tools import protoc

SERVER = Path(__file__).resolve().parents[1]
PROTO = SERVER.parent / "proto"
OUT = SERVER / "gen"


def generate(proto_dir: Path, out: Path) -> list[Path]:
    """Regenerates `out` from every .proto under `proto_dir`; returns the sources."""
    sources = sorted(proto_dir.rglob("*.proto"))
    if not sources:
        raise SystemExit(f"error: no .proto files under {proto_dir}")
    if out.name != "gen":
        raise SystemExit(f"error: refusing to replace {out}; the output folder must be named gen")
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    # google/protobuf/*.proto ship with grpcio-tools.
    well_known = resources.files("grpc_tools") / "_proto"
    args = [
        "protoc",
        f"-I{proto_dir}",
        f"-I{well_known}",
        f"--python_out={out}",
        f"--pyi_out={out}",
        *(source.relative_to(proto_dir).as_posix() for source in sources),
    ]
    if protoc.main(args) != 0:
        raise SystemExit("error: protoc failed")
    return sources


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="devtools.gen_proto", description=__doc__)
    parser.add_argument("--proto-dir", type=Path, default=PROTO)
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args(argv)
    sources = generate(args.proto_dir.resolve(), args.out.resolve())
    print(f"generated {len(sources)} files into {args.out}", file=sys.stderr)  # noqa: T201
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
