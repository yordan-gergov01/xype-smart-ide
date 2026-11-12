# Build from server/ with the proto/ folder as a named context:
#   docker build -f backend/Dockerfile --build-context proto=../proto .
#
# Stages:
#   deps     virtual environment with the backend's pinned, hash-checked dependencies
#   codegen  the protobuf modules from proto/ (devtools.gen_proto); only its output is kept
#   runtime  the production image: common_lib, backend and the migrations, non-root;
#            the migrate service runs `alembic upgrade head` from it
#   test     adds the development dependencies and the tests; never shipped

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
COPY requirements/backend.txt /tmp/requirements/backend.txt
RUN python -m venv /opt/venv \
    && pip install --require-hashes -r /tmp/requirements/backend.txt

FROM deps AS codegen
COPY requirements/codegen.txt /tmp/requirements/codegen.txt
RUN pip install --require-hashes -r /tmp/requirements/codegen.txt
COPY devtools/__init__.py devtools/gen_proto.py ./devtools/
COPY --from=proto . /proto
RUN python -m devtools.gen_proto --proto-dir /proto --out /gen

FROM base AS runtime
COPY --from=deps /opt/venv /opt/venv
COPY --from=codegen /gen ./gen
ENV PYTHONPATH=/app/gen
COPY common_lib ./common_lib
COPY backend ./backend
COPY migrations ./migrations
COPY alembic.ini ./
USER 10001:10001
# 8000: REST (HTTPS); 50051: the gRPC Session stream (TLS), for the edge proxy only.
EXPOSE 8000 50051
HEALTHCHECK --interval=10s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import ssl, urllib.request; urllib.request.urlopen('https://127.0.0.1:8000/healthz', timeout=3, context=ssl.create_default_context(cafile='/certs/ca.crt'))"]
# Uvicorn's access log is replaced by the backend's metadata-only access line. The
# edge proxy sets no X-Forwarded-* headers; client addresses are not needed.
# HTTPS only, with the certificate mounted at /certs (ADR-0036); without it the
# server does not start.
CMD ["uvicorn", "backend.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", \
     "--ssl-certfile", "/certs/tls.crt", "--ssl-keyfile", "/certs/tls.key", \
     "--no-access-log", "--no-proxy-headers", "--timeout-graceful-shutdown", "10"]

FROM deps AS test
COPY requirements/dev.txt /tmp/requirements/dev.txt
RUN pip install --require-hashes -r /tmp/requirements/dev.txt
ENV PYTHONPATH=/app:/app/gen
COPY --from=codegen /gen ./gen
COPY pyproject.toml alembic.ini ./
COPY common_lib ./common_lib
COPY backend ./backend
COPY migrations ./migrations
COPY devtools ./devtools
COPY tests ./tests
RUN chown app:app /app
USER 10001:10001
CMD ["pytest", "-p", "no:cacheprovider"]
