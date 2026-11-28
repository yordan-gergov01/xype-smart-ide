"""The running end-to-end stack (`deploy/e2e/compose.e2e.yml`), reached only through the
`docker` CLI: commands run inside the containers with the containers' own environment,
so no stack password ever passes through the test (docs/runbooks/e2e.md)."""

import os
import shlex
import subprocess
from dataclasses import dataclass

PROJECT_VAR = "XYPE_E2E_PROJECT"
PORT_VAR = "XYPE_E2E_PORT"
TIMEOUT_S = 120
# The superuser over the local socket, with the password from the container's environment.
PG_AUTH = 'PGPASSWORD="$POSTGRES_PASSWORD"'
PG_TARGET = '-U "$POSTGRES_USER" -d "$POSTGRES_DB"'
# Every service of the project whose log the scan reads.
SERVICES = (
    "edge",
    "backend",
    "indexer",
    "postgres",
    "redis",
    "keystore",
    "rabbitmq",
    "ollama",
    "otel-collector",
)


class StackError(Exception):
    """A command in the stack failed; the message names the command, never its output."""


@dataclass(frozen=True, slots=True)
class Stack:
    project: str
    port: int

    @classmethod
    def from_environment(cls) -> "Stack | None":
        project = os.environ.get(PROJECT_VAR)
        if not project:
            return None
        return cls(project=project, port=int(os.environ.get(PORT_VAR, "18443")))

    @property
    def server_url(self) -> str:
        return f"https://127.0.0.1:{self.port}"

    def container(self, service: str) -> str:
        return f"{self.project}-{service}-1"

    def exec(self, service: str, script: str, *, stdin: bytes | None = None) -> bytes:
        """Runs `script` with `sh -c` in the service's container and returns its stdout."""
        command = ["docker", "exec", "-i", self.container(service), "sh", "-c", script]
        return _run(command, stdin=stdin, label=f"{service}: {script.split(maxsplit=1)[0]}")

    def logs(self, service: str) -> bytes:
        command = ["docker", "logs", self.container(service)]
        result = subprocess.run(command, capture_output=True, timeout=TIMEOUT_S, check=False)  # noqa: S603
        if result.returncode != 0:
            raise StackError(f"docker logs {service} failed")
        return result.stdout + result.stderr

    def admin(self, *args: str) -> str:
        """`backend.admin_cli` inside the backend container; its output may hold a secret."""
        script = "python -m backend.admin_cli " + shlex.join(args)
        return self.exec("backend", script).decode()

    def sql(self, query: str, **params: str) -> list[list[str]]:
        """Rows of a query as the superuser over the local socket (tab-separated, no header).
        Values go in as psql variables (`:'name'` in the query), never into its text."""
        variables = " ".join(f"-v {name}={shlex.quote(value)}" for name, value in params.items())
        script = f'{PG_AUTH} psql {PG_TARGET} -AtF "\t" -v ON_ERROR_STOP=1 {variables}'
        out = self.exec("postgres", script, stdin=query.encode()).decode()
        return [line.split("\t") for line in out.splitlines() if line]


def _run(command: list[str], *, stdin: bytes | None, label: str) -> bytes:
    result = subprocess.run(  # noqa: S603 - fixed docker commands
        command, input=stdin, capture_output=True, timeout=TIMEOUT_S, check=False
    )
    if result.returncode != 0:
        raise StackError(f"{label} exited with {result.returncode}")
    return result.stdout
