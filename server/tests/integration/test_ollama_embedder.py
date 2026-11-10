"""OllamaEmbedder against the real qwen3-embedding:0.6b (ADR-0002).

Uses the models compose already downloaded (volume `xype_ollama_data`, read-only),
hardened like the compose `ollama` service (non-root, read-only root filesystem, no
capabilities, cloud models off, memory limit) and published on 127.0.0.1 only. It
sits on Docker's default bridge, so unlike compose it has outbound access while the
test runs; it never downloads a model, and without the volume it fails and says how
to get it.
"""

import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest
import yaml
from support.contracts import embedder as contract
from testcontainers.core.container import DockerContainer
from testcontainers.core.wait_strategies import HttpWaitStrategy

from common_lib.domain import ModelRef
from common_lib.embeddings.ollama import OllamaEmbedder
from common_lib.secret_bytes import SecretBytes

pytestmark = [pytest.mark.integration, pytest.mark.models]

COMPOSE = yaml.safe_load(
    (Path(__file__).resolve().parents[3] / "deploy" / "docker-compose.yml").read_text("utf-8")
)
IMAGE = COMPOSE["services"]["ollama"]["image"]
VOLUME = "xype_ollama_data"
MODEL = ModelRef("qwen3-embedding:0.6b", "ollama-q8")


@pytest.fixture(scope="module")
def ollama_url() -> Iterator[str]:
    found = subprocess.run(  # noqa: S603 - fixed arguments
        ["docker", "volume", "inspect", VOLUME],  # noqa: S607 - the docker CLI on PATH
        capture_output=True,
        check=False,
    )
    if found.returncode != 0:
        pytest.fail(f"volume {VOLUME} missing: start deploy/docker-compose.yml once to pull models")
    container = (
        DockerContainer(IMAGE, user="10001:10001")
        # The second argument is passed to Docker as the tmpfs mount options.
        .with_tmpfs_mount("/tmp", "uid=10001,gid=10001")  # noqa: S108 - tmpfs home
        .with_env("HOME", "/tmp")  # noqa: S108 - tmpfs home, as in compose
        .with_env("OLLAMA_MODELS", "/ollama/models")
        .with_env("OLLAMA_NOPRUNE", "1")
        .with_env("OLLAMA_NO_CLOUD", "1")
        .with_volume_mapping(VOLUME, "/ollama", "ro")
        .with_kwargs(
            read_only=True,
            cap_drop=["ALL"],
            security_opt=["no-new-privileges:true"],
            mem_limit="2304m",
        )
        .waiting_for(HttpWaitStrategy(11434, "/api/tags").with_startup_timeout(120))
    )
    # Publish on loopback only; the builder takes ports, the Docker SDK takes (host_ip, port).
    # reason: typed as int | None in testcontainers; the Docker SDK accepts the tuple.
    container.ports["11434"] = ("127.0.0.1", None)  # type: ignore[assignment]
    with container:
        yield f"http://{container.get_container_host_ip()}:{container.get_exposed_port(11434)}"


async def test_ollama_embedder_meets_the_contract(ollama_url: str) -> None:
    embedder = OllamaEmbedder(base_url=ollama_url, model=MODEL, timeout_s=180)
    try:
        await contract.assert_embeds_documents_and_wipes_them(embedder)
        await contract.assert_is_deterministic(embedder)
        await contract.assert_embeds_queries(embedder)
    finally:
        await embedder.aclose()
    closed = OllamaEmbedder(base_url="http://127.0.0.1:1", model=MODEL, timeout_s=2)
    try:
        await contract.assert_wipes_inputs_on_failure(closed)
    finally:
        await closed.aclose()


async def test_queries_are_closer_to_the_code_that_answers_them(ollama_url: str) -> None:
    embedder = OllamaEmbedder(base_url=ollama_url, model=MODEL, timeout_s=180)
    try:
        add, invoice = await embedder.embed_documents([SecretBytes(c) for c in contract.CHUNKS])
        query = await embedder.embed_query(
            "How are two numbers added?", instruction=contract.INSTRUCTION
        )
    finally:
        await embedder.aclose()

    def cosine(a: list[float] | tuple[float, ...], b: list[float] | tuple[float, ...]) -> float:
        dot = sum(x * y for x, y in zip(a, b, strict=True))
        return float(dot / ((sum(x * x for x in a) ** 0.5) * (sum(y * y for y in b) ** 0.5)))

    assert cosine(list(query), list(add)) > cosine(list(query), list(invoice))
