"""Run a tool from the server's virtual environment inside server/.

Used by pre-commit so mypy and import-linter work without activating the venv:
    python tools/server_tool.py mypy
"""

import subprocess
import sys
from pathlib import Path

SERVER = Path(__file__).resolve().parents[1] / "server"
BIN = SERVER / ".venv" / ("Scripts" if sys.platform == "win32" else "bin")


def main(argv: list[str]) -> int:
    if not argv:
        print("usage: server_tool.py <tool> [args...]", file=sys.stderr)
        return 2
    if Path(argv[0]).name != argv[0]:
        print(f"not a tool name: {argv[0]!r}", file=sys.stderr)
        return 2
    exe = BIN / (argv[0] + (".exe" if sys.platform == "win32" else ""))
    if not exe.exists():
        print(
            f"{exe} not found. Create the server venv first (server/README.md, "
            "'Local development setup').",
            file=sys.stderr,
        )
        return 1
    return subprocess.run([str(exe), *argv[1:]], cwd=SERVER, check=False).returncode  # noqa: S603


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
