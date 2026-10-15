FROM node:24-bookworm-slim AS frontend
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./frontend/
RUN cd frontend && npm ci
COPY frontend ./frontend
COPY plugins ./plugins
COPY docs ./docs
RUN cd frontend && npm run build

FROM ghcr.io/astral-sh/uv:0.12.7 AS uv
FROM python:3.12-slim-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends bash ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --uid 10001 --create-home oaw
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /opt/oaw
COPY backend/pyproject.toml backend/uv.lock ./backend/
# Export locked versions and hashes so an optional build-time mirror can serve
# the same artifacts without rewriting the repository's lockfile.
ARG OAW_PYPI_INDEX=https://pypi.org/simple
RUN --mount=type=cache,target=/root/.cache/uv \
    uv export --frozen --no-dev --project backend --extra hosting --extra aliyun --extra adk --extra litellm \
      --format requirements-txt --output-file /tmp/oaw-requirements.txt --quiet \
    && uv venv backend/.venv \
    && UV_LINK_MODE=copy uv pip sync --python backend/.venv/bin/python --require-hashes \
      --index-url "$OAW_PYPI_INDEX" /tmp/oaw-requirements.txt
# Install the same Pack-owned orchestration dependencies used by the personal
# edition. They are immutable image inputs, never installed by end users.
COPY plugins/matcreator ./plugins/matcreator
RUN --mount=type=cache,target=/root/.cache/uv \
    UV_LINK_MODE=copy UV_DEFAULT_INDEX="$OAW_PYPI_INDEX" \
      backend/.venv/bin/python plugins/matcreator/oaw_matcreator/native_environment.py
COPY backend ./backend
COPY open_agent_world ./open_agent_world
COPY plugins/structure_viewer ./plugins/structure_viewer
COPY plugins/sqlite ./plugins/sqlite
COPY --from=frontend /build/frontend/dist ./frontend/dist
ENV PATH="/opt/oaw/backend/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    OPEN_AGENT_WORLD_DATA_ROOT=/var/lib/oaw
RUN mkdir -p /var/lib/oaw && chown oaw:oaw /var/lib/oaw
USER oaw
EXPOSE 8080
CMD ["uvicorn", "backend.cloud.aliyun.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8080", "--workers", "1", "--no-proxy-headers"]
