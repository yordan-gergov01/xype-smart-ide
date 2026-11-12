"""Keep deploy/docker-compose.yml, deploy/env/.env.example and the hardening rules in step."""

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

from backend.core.settings import BackendSettings

ROOT = Path(__file__).resolve().parents[4]
DEPLOY = ROOT / "deploy"
COMPOSE: dict[str, Any] = yaml.safe_load((DEPLOY / "docker-compose.yml").read_text("utf-8"))
SERVICES: dict[str, dict[str, Any]] = COMPOSE["services"]
NETWORKS: dict[str, dict[str, Any]] = COMPOSE["networks"]
ENV_EXAMPLE = (DEPLOY / "env" / ".env.example").read_text("utf-8")

# Images without a shell or HTTP client, and one-shot jobs, cannot have a healthcheck.
NO_HEALTHCHECK = {"otel-collector", "ollama-pull", "migrate"}
# Images whose default user is already non-root (checked when the image was pinned).
NON_ROOT_BY_IMAGE = {"otel-collector", "prometheus", "grafana"}
# Writes the root-owned model volume once, then exits (ADR-0034).
ROOT_ALLOWED = {"ollama-pull"}
# The only services allowed on networks with a route outside the stack.
OPEN_NETWORK_MEMBERS = {
    "egress": {"ollama-pull"},
    "ui": {"prometheus", "grafana"},
    "edge": {"edge"},
}
# Stores, broker and models are reached only from inside the stack.
NO_PORTS = {"postgres", "redis", "keystore", "rabbitmq", "ollama", "backend", "migrate"}
PINNED_IMAGE = re.compile(r"^[\w./-]+:[\w.-]+@sha256:[0-9a-f]{64}$")


def compose_variables() -> set[str]:
    text = (DEPLOY / "docker-compose.yml").read_text("utf-8")
    return set(re.findall(r"(?<!\$)\$\{([A-Z0-9_]+)", text))


def example_variables() -> set[str]:
    return set(re.findall(r"^([A-Z0-9_]+)=", ENV_EXAMPLE, re.MULTILINE))


def test_env_example_matches_compose_variables() -> None:
    assert compose_variables() == example_variables()


@pytest.mark.parametrize("name", sorted(SERVICES))
def test_service_is_hardened(name: str) -> None:
    service = SERVICES[name]
    if service.get("image") == SERVICES["backend"]["image"]:
        service = {**SERVICES["backend"], **service}  # runs the backend's built image
    if "build" in service:
        dockerfile = ROOT / "server" / service["build"]["dockerfile"]
        base = re.search(r"^ARG PYTHON_IMAGE=(\S+)$", dockerfile.read_text("utf-8"), re.MULTILINE)
        assert base, "built images start from a pinned base image"
        assert PINNED_IMAGE.match(base.group(1))
    else:
        assert PINNED_IMAGE.match(service["image"]), "image must be pinned by tag and digest"
    assert service["read_only"] is True
    assert service["cap_drop"] == ["ALL"]
    assert "cap_add" not in service
    assert "no-new-privileges:true" in service["security_opt"]
    assert service["mem_limit"]
    if name not in NO_HEALTHCHECK:
        assert "test" in service["healthcheck"]


@pytest.mark.parametrize("name", sorted(SERVICES))
def test_service_runs_as_non_root(name: str) -> None:
    if name in ROOT_ALLOWED or name in NON_ROOT_BY_IMAGE:
        assert "user" not in SERVICES[name]
        return
    user = str(SERVICES[name]["user"])
    assert not user.startswith(("0", "root")), user


@pytest.mark.parametrize("name", sorted(SERVICES))
def test_published_ports_bind_to_loopback_only(name: str) -> None:
    ports = SERVICES[name].get("ports", [])
    if name in NO_PORTS:
        assert ports == []
    for port in ports:
        assert str(port).startswith("127.0.0.1:"), port


@pytest.mark.parametrize("name", sorted(SERVICES))
def test_only_allowed_services_join_networks_with_outside_access(name: str) -> None:
    for network in SERVICES[name]["networks"]:
        if not NETWORKS[network].get("internal"):
            assert name in OPEN_NETWORK_MEMBERS.get(network, set()), (name, network)


def test_ollama_never_uses_cloud_models() -> None:
    for name in ("ollama", "ollama-pull"):
        assert SERVICES[name]["environment"]["OLLAMA_NO_CLOUD"] == "1"
    assert "cloud" not in ENV_EXAMPLE.split("OLLAMA_PULL_MODELS=")[1].splitlines()[0]


def test_collector_debug_exporter_logs_no_span_contents() -> None:
    collector = yaml.safe_load((DEPLOY / "otel" / "collector.yaml").read_text("utf-8"))
    assert collector["exporters"]["debug"]["verbosity"] == "basic"


def test_default_memory_budget_leaves_room_for_the_workers() -> None:
    # One-shot jobs are left out: the model download finishes before ollama starts.
    units = {"m": 1, "g": 1024}
    default = [s for s in SERVICES.values() if "profiles" not in s and s.get("restart") != "no"]
    total = sum(int(s["mem_limit"][:-1]) * units[s["mem_limit"][-1]] for s in default)
    # 4 GiB in total, of which about 600 MiB are kept for the workers (llm, indexer).
    assert total <= 4096 - 600, f"{total} MiB"


# --- TLS with the local dev CA (ADR-0036) -------------------------------------------
# Services that terminate TLS, each with its own certificate directory.
TLS_SERVERS = {"edge", "backend", "postgres", "redis", "keystore", "rabbitmq"}
# Services that only verify servers and get the CA certificate alone.
TLS_CLIENTS_ONLY = {"migrate", "prometheus"}


def _sources(service: dict[str, Any]) -> list[str]:
    return [v if isinstance(v, str) else v["source"] for v in service.get("volumes", [])]


@pytest.mark.parametrize("name", sorted(SERVICES))
def test_services_get_only_their_own_certificate(name: str) -> None:
    mounts = [v for v in SERVICES[name].get("volumes", []) if isinstance(v, dict)]
    mounts = [m for m in mounts if m["type"] == "bind"]
    if name in TLS_SERVERS:
        expected = f"./certs/{name}"
    elif name in TLS_CLIENTS_ONLY:
        expected = "./certs/ca.crt"
    else:
        assert mounts == []
        return
    assert [m["source"] for m in mounts if m["type"] == "bind"] == [expected]
    (mount,) = mounts
    assert mount["read_only"] is True
    # Linux: a missing dev CA fails the start. Docker Desktop creates an empty folder
    # instead, and the services then stop on the missing files (local-dev runbook).
    assert mount["bind"]["create_host_path"] is False
    assert not any("ca.key" in source for source in _sources(SERVICES[name]))


@pytest.mark.parametrize("name", ["backend", "migrate"])
def test_store_clients_verify_against_the_dev_ca(name: str) -> None:
    assert SERVICES[name]["environment"]["TLS_CA_FILE"] == "/certs/ca.crt"


def test_stores_have_no_plaintext_listener() -> None:
    docker = DEPLOY / "docker"
    redis = (docker / "redis" / "entrypoint.sh").read_text("utf-8")
    assert re.search(r"^port 0$", redis, re.MULTILINE)
    assert "tls-port 6379" in redis
    broker = (docker / "rabbitmq" / "conf.d" / "20-xype.conf").read_text("utf-8")
    assert "listeners.tcp = none" in broker
    hba = (docker / "postgres" / "pg_hba.conf").read_text("utf-8").splitlines()
    rules = [line.split() for line in hba if line and not line.startswith("#")]
    assert {r[0] for r in rules} == {"local", "hostssl", "hostnossl"}
    assert rules[-1] == ["hostnossl", "all", "all", "all", "reject"]
    assert "ssl=on" in SERVICES["postgres"]["command"]
    assert "hba_file=/opt/xype/pg_hba.conf" in SERVICES["postgres"]["command"]


def test_edge_and_backend_serve_https_only() -> None:
    nginx = (DEPLOY / "docker" / "edge" / "nginx.conf").read_text("utf-8")
    assert re.findall(r"^\s*listen\s+(.+);", nginx, re.MULTILINE) == ["8443 ssl"]
    assert "proxy_ssl_verify on;" in nginx
    assert "http://" not in nginx
    assert SERVICES["edge"]["ports"] == ["127.0.0.1:${BACKEND_PORT:-8000}:8443"]
    dockerfile = (ROOT / "server" / "backend" / "Dockerfile").read_text("utf-8")
    assert '"--ssl-certfile", "/certs/tls.crt"' in dockerfile
    for name in ("backend", "edge"):
        assert "https://127.0.0.1" in " ".join(SERVICES[name]["healthcheck"]["test"])


def test_prometheus_scrapes_the_backend_over_verified_https() -> None:
    config = yaml.safe_load((DEPLOY / "prometheus" / "prometheus.yml").read_text("utf-8"))
    (job,) = [j for j in config["scrape_configs"] if j["job_name"] == "backend"]
    assert job["scheme"] == "https"
    assert job["tls_config"] == {"ca_file": "/etc/prometheus/ca.crt", "server_name": "backend"}


def test_the_key_store_is_reached_only_over_its_own_network() -> None:
    """Session keys never share a store or a network with the ciphertext (ADR-0015)."""
    assert SERVICES["keystore"]["networks"] == ["keys"]
    assert COMPOSE["networks"]["keys"] == {"internal": True}
    members = {name for name, s in SERVICES.items() if "keys" in s.get("networks", [])}
    assert members == {"keystore", "backend"}
    assert SERVICES["keystore"]["environment"]["REDIS_MODE"] == "keystore"
    assert SERVICES["redis"]["environment"]["REDIS_MODE"] == "cache"


def test_the_key_store_never_evicts_and_scopes_the_backend_to_its_keys() -> None:
    script = (DEPLOY / "docker" / "redis" / "entrypoint.sh").read_text("utf-8")
    keystore = script[script.index("    keystore)") : script.index("    *)")]
    assert "maxmemory-policy noeviction" in keystore
    assert 'scope="resetchannels ~xype:sk*"' in keystore
    assert 'extra=""' in keystore


def test_the_session_stream_goes_through_the_edge_over_verified_tls() -> None:
    """grpcs to the backend with its certificate checked, the stream not cut by the REST
    body limit or by timeouts shorter than the backend's own (ADR-0013, ADR-0036)."""
    nginx = (DEPLOY / "docker" / "edge" / "nginx.conf").read_text("utf-8")
    block = nginx[nginx.index("location = /xype.v1.SessionService/Session {") :]
    block = block[: block.index("}")]
    for line in (
        "grpc_pass grpcs://backend_grpc;",
        "grpc_ssl_verify on;",
        "grpc_ssl_trusted_certificate /certs/ca.crt;",
        "grpc_ssl_name backend;",
        "limit_req zone=stream_per_ip",
        "limit_conn streams_open_per_ip",
        "client_max_body_size 0;",
        'grpc_set_header traceparent "";',
    ):
        assert line in block
    assert re.search(r"upstream backend_grpc \{\s*server backend:50051;", nginx)
    timeouts = re.findall(r"(?:client_body|grpc_read|grpc_send)_timeout (\d+)s;", block)
    assert len(timeouts) == 3
    # Even the largest idle timeout the settings allow stays below the proxy's.
    idle = BackendSettings.model_fields["stream_idle_timeout_s"].metadata
    longest = max(m.le for m in idle if hasattr(m, "le"))
    assert all(int(t) > longest for t in timeouts)
    backend = SERVICES["backend"]
    assert backend["environment"]["GRPC_LISTEN_ADDRESS"] == "0.0.0.0:50051"
    assert backend["build"]["additional_contexts"] == {"proto": "../proto"}
