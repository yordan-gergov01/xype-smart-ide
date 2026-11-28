"""Throwaway configuration for the end-to-end stack (Phase 2 slice 12).

    python tools/server_tool.py python -m devtools.e2e_stack --out <dir>

Writes `<dir>/stack.vars` for `docker compose --env-file`: the environment template with
every placeholder password replaced by a random value and fresh auth, pepper and KEK
keys. The values are new on every run and belong only to the `xype-e2e` compose project
(`deploy/e2e/compose.e2e.yml`), never to the developer's own stack. `--out` must be a
private directory outside the repository (refused otherwise); the file is deleted with
the stack.
"""

import argparse
import os
import secrets
from pathlib import Path

from common_lib.settings import new_keyed_secret

REPOSITORY = Path(__file__).resolve().parents[2]
TEMPLATE = REPOSITORY / "deploy" / "env" / ".env.example"
PLACEHOLDER = "dev-only-change-me"
# Keys the stack refuses to start without.
GENERATED_KEYS = {
    "AUTH_SIGNING_KEY_CURRENT": "e2e-signing-1",
    "AUTH_PEPPER_CURRENT": "e2e-pepper-1",
    "KEK_CURRENT": "e2e-kek-1",
}
# Differs from the developer's stack, so both never fight over a port; DEBUG logs make
# the privacy scan stricter, and traces go to the collector the scan reads.
OVERRIDES = {
    "BACKEND_PORT": "18443",
    "LOG_LEVEL": "DEBUG",
    "OTEL_EXPORTER_OTLP_ENDPOINT": "http://otel-collector:4318",
}


def render(template: str) -> str:
    """The template with every placeholder and generated key filled in."""
    return "\n".join(_render_line(line) for line in template.splitlines()) + "\n"


def _render_line(line: str) -> str:
    name, sep, value = line.partition("=")
    if not sep or line.startswith("#"):
        return line
    if PLACEHOLDER in value:
        value = secrets.token_urlsafe(24)
    elif name in GENERATED_KEYS:
        value = new_keyed_secret(GENERATED_KEYS[name])
    elif name in OVERRIDES:
        value = OVERRIDES[name]
    return f"{name}={value}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="devtools.e2e_stack", description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    out: Path = args.out.resolve()
    if out.is_relative_to(REPOSITORY):
        parser.error("--out must be outside the repository")
    out.mkdir(parents=True, exist_ok=True)
    target = out / "stack.vars"
    target.unlink(missing_ok=True)
    # Created owner-only from the start (POSIX); on Windows the directory's ACL applies.
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as file:
        file.write(render(TEMPLATE.read_text(encoding="utf-8")))
    print(target)  # noqa: T201 - CLI output, the path only
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
