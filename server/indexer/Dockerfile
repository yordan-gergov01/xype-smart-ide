# Build from server/ with the shared scrub rules as a named context:
#   docker build -f indexer/Dockerfile --build-context scrub_rules=../packages/scrub-rules .
#
# Stages:
#   deps     virtual environment with the indexer's pinned, hash-checked dependencies
#   runtime  the production image: common_lib, indexer and the scrub rules, non-root;
#            no database driver, no gRPC, no web framework (ADR-0015)

ARG PYTHON_IMAGE=python:3.12.15-slim-trixie@sha256:a0f57064cbdcd6285fa45f51237ab0b35ea8ebaed06a1f5973e89e19dfb86704

FROM ${PYTHON_IMAGE} AS base
# No .pyc files: the runtime filesystem is read-only. Unbuffered output for logs.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PATH=/opt/venv/bin:$PATH
RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --no-create-home --shell /usr/sbin/nologin app
WORKDIR /app

FROM base AS deps
COPY requirements/indexer.txt /tmp/requirements/indexer.txt
RUN python -m venv /opt/venv \
    && pip install --require-hashes -r /tmp/requirements/indexer.txt

FROM base AS runtime
COPY --from=deps /opt/venv /opt/venv
COPY common_lib ./common_lib
COPY indexer ./indexer
# The same rule file the client uses (ADR-0019); the server re-check runs it.
COPY --from=scrub_rules rules.json /app/scrub-rules/rules.json
ENV SCRUB_RULES_FILE=/app/scrub-rules/rules.json
USER 10001:10001
# Health and metrics, plain HTTP on the internal networks (no payload; ADR-0036).
EXPOSE 9100
HEALTHCHECK --interval=10s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:9100/healthz', timeout=3)"]
CMD ["python", "-m", "indexer.main"]
