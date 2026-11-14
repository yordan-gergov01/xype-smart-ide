"""Testcontainers for the stores, configured as deploy/docker-compose.yml runs them: the
compose file's images, users, entrypoints, commands, tmpfs mounts and bind-mounted
scripts and configuration (TLS-only listeners), with certificates from a throwaway dev CA
instead of deploy/certs/ (ADR-0036). Named data volumes are left out: tests start empty.
"""

import re
from pathlib import Path
from typing import Any

import yaml
from testcontainers.core.container import DockerContainer
from testcontainers.core.wait_strategies import LogMessageWaitStrategy

from support.tls import certs_dir

DEPLOY = Path(__file__).resolve().parents[3] / "deploy"
COMPOSE: dict[str, Any] = yaml.safe_load(  # reason: parsed YAML
    (DEPLOY / "docker-compose.yml").read_text("utf-8")
)
SERVICES: dict[str, dict[str, Any]] = COMPOSE["services"]  # reason: parsed YAML
STARTUP_SECONDS = 120
INITDB = "/docker-entrypoint-initdb.d"


def _mounts(name: str, *, skip: set[str]) -> dict[Path, str]:
    """The service's bind mounts: the repo's ./docker files, and its certificate folder."""
    mounts: dict[Path, str] = {}
    for volume in SERVICES[name].get("volumes", []):
        if isinstance(volume, dict):  # a dev CA bind mount: ./certs/<path>
            relative = Path(volume["source"]).relative_to("certs")
            mounts[certs_dir() / relative] = volume["target"]
            continue
        source, target, *_ = volume.split(":")
        if source.startswith("./") and target not in skip:
            mounts[(DEPLOY / source).resolve()] = target
    return mounts


def _service(name: str, *, skip: set[str] | None = None) -> DockerContainer:
    service = SERVICES[name]
    # One call: `with_kwargs` replaces earlier keyword arguments.
    container = DockerContainer(service["image"]).with_kwargs(
        user=service["user"],
        entrypoint=service["entrypoint"],
        cap_drop=["ALL"],
        security_opt=["no-new-privileges:true"],
        **({"hostname": service["hostname"]} if "hostname" in service else {}),
    )
    for entry in service.get("tmpfs", []):
        path, _, options = entry.partition(":")
        # The "size" argument is passed to Docker as the tmpfs options string.
        container = container.with_tmpfs_mount(path, options or None)
    for host, target in _mounts(name, skip=skip or set()).items():
        container = container.with_volume_mapping(str(host), target, "ro")
    if "command" in service:
        container = container.with_command(service["command"])
    return container


def _ready(
    container: DockerContainer, port: int, message: str | re.Pattern[str]
) -> DockerContainer:
    wait = LogMessageWaitStrategy(message).with_startup_timeout(STARTUP_SECONDS)
    return container.with_exposed_ports(port).waiting_for(wait)


def postgres(env: dict[str, str], *, initdb: bool = False) -> DockerContainer:
    """Port 5432, TLS only. `env` sets the POSTGRES_* variables of the image; `initdb`
    runs the compose file's role setup."""
    container = _service("postgres", skip=set() if initdb else {INITDB}).with_envs(**env)
    # Only the real server listens on TCP; the temporary initialisation server, which
    # also reports "ready", listens on the socket only.
    ready = re.compile(r"listening on IPv4 address.*ready to accept connections", re.DOTALL)
    return _ready(container, 5432, ready)


def redis(env: dict[str, str], *, service: str = "redis") -> DockerContainer:
    """Port 6379, TLS only: the cache, or with `service="keystore"` the key store. `env`
    sets REDIS_PASSWORD and the backend's ACL user; the mode comes from the compose file."""
    mode = SERVICES[service]["environment"]["REDIS_MODE"]
    container = _service(service).with_envs(**env, REDIS_MODE=mode)
    return _ready(container, 6379, "Ready to accept connections tls")


def rabbitmq(env: dict[str, str]) -> DockerContainer:
    """Port 5671 (AMQPS) only, with the index topology. `env` sets the vhost and the
    backend's and the indexer's users."""
    return _ready(_service("rabbitmq").with_envs(**env), 5671, "Server startup complete")
