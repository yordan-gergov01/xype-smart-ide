"""Admin CLI for client credentials (ADR-0014, Q11) and repository registration
(ADR-0016): a composition root like `backend.main`, run inside the backend container.

    docker compose ... exec backend python -m backend.admin_cli create-team "Web"
    docker compose ... exec backend python -m backend.admin_cli create-client \\
        --team "Web" --email dev@example.com --name "Dev One"
    docker compose ... exec backend python -m backend.admin_cli disable-client xc_0123abcd...
    docker compose ... exec backend python -m backend.admin_cli rotate-secret xc_0123abcd...
    docker compose ... exec backend python -m backend.admin_cli register-repo \\
        --team "Web" --name sample-app --remote-url https://git.example.com/web/sample-app.git
    python tools/server_tool.py python -m backend.admin_cli new-key auth-2026-11

A client secret is printed once, to this terminal only; it is never logged or stored.
`new-key` prints a fresh `<id>:<base64url>` value for AUTH_SIGNING_KEY_* or AUTH_PEPPER_*
and needs no settings. `register-repo` strips any credentials from the remote URL and
creates the repository's HMAC key, which the CLI never prints.
"""

import argparse
import asyncio
import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from backend.core.auth import auth_dependencies, client_admin
from backend.core.connections import (
    create_database,
    create_keystore_client,
    create_redis_client,
)
from backend.core.repositories import repository_admin
from backend.core.settings import BackendSettings
from backend.services.auth import ClientAdmin, NewClient, Role
from backend.services.repositories import RepositoryAdmin
from common_lib.clock import SystemClock
from common_lib.errors import XypeError
from common_lib.logging_config import configure_logging
from common_lib.settings import load_settings, new_keyed_secret


@dataclass(frozen=True, slots=True)
class Admins:
    clients: ClientAdmin
    repositories: RepositoryAdmin


type Command = Callable[[Admins, argparse.Namespace], Awaitable[str]]


def _show(client: NewClient) -> str:
    return (
        f"client_id:     {client.client_id}\n"
        f"client_secret: {client.client_secret}\n"
        "Shown once. Hand it to the developer over a private channel."
    )


async def _create_team(admin: Admins, args: argparse.Namespace) -> str:
    return f"team_id: {await admin.clients.create_team(args.name)}"


async def _create_client(admin: Admins, args: argparse.Namespace) -> str:
    role: Role = "admin" if args.admin else "developer"
    client = await admin.clients.create_client(
        team=args.team, email=args.email, display_name=args.name, role=role
    )
    return _show(client)


async def _disable_client(admin: Admins, args: argparse.Namespace) -> str:
    return f"disabled user {await admin.clients.disable_client(args.client_id)}"


async def _rotate_secret(admin: Admins, args: argparse.Namespace) -> str:
    return _show(await admin.clients.rotate_secret(args.client_id))


async def _register_repo(admin: Admins, args: argparse.Namespace) -> str:
    registered = await admin.repositories.register(
        team=args.team, name=args.name, remote_url=args.remote_url, is_template=args.template
    )
    return f"repository_id: {registered.repository_id}"


COMMANDS: dict[str, Command] = {
    "create-team": _create_team,
    "create-client": _create_client,
    "disable-client": _disable_client,
    "rotate-secret": _rotate_secret,
    "register-repo": _register_repo,
}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="backend.admin_cli", description="Clients and repositories"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("create-team").add_argument("name")
    client = commands.add_parser("create-client")
    client.add_argument("--team", required=True)
    client.add_argument("--email", required=True)
    client.add_argument("--name", required=True)
    client.add_argument("--admin", action="store_true")
    commands.add_parser("disable-client").add_argument("client_id")
    commands.add_parser("rotate-secret").add_argument("client_id")
    repo = commands.add_parser("register-repo")
    repo.add_argument("--team", required=True)
    repo.add_argument("--name", required=True)
    repo.add_argument("--remote-url")
    repo.add_argument("--template", action="store_true")
    commands.add_parser("new-key").add_argument("key_id")
    return parser


async def _run(command: Command, args: argparse.Namespace) -> str:
    settings = load_settings(BackendSettings)
    # INFO: the admin actions are logged (ids only) as an audit trail.
    configure_logging(service="admin-cli", level="INFO", log_format=settings.log_format)
    database, redis = create_database(settings), create_redis_client(settings)
    keystore = create_keystore_client(settings)
    try:
        # Disabling a client or rotating its secret also deletes its session keys.
        deps = auth_dependencies(settings, database, redis, keystore)
        clock = SystemClock()
        admins = Admins(
            client_admin(settings, deps, clock),
            repository_admin(settings, database, deps.users, clock),
        )
        return await command(admins, args)
    finally:
        await database.dispose()
        await redis.aclose()
        await keystore.aclose()


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "new-key":
            output = new_keyed_secret(args.key_id)
        else:
            output = asyncio.run(_run(COMMANDS[args.command], args))
    except (XypeError, ValueError) as exc:  # fixed messages only, never a value
        print(f"error: {exc}", file=sys.stderr)  # noqa: T201 - CLI output
        return 1
    print(output)  # noqa: T201 - CLI output
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
