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
COPY backend ./backend
COPY open_agent_world ./open_agent_world
COPY plugins/structure_viewer ./plugins/structure_viewer
COPY plugins/matcreator ./plugins/matcreator
COPY plugins/sqlite ./plugins/sqlite
RUN uv sync --frozen --no-dev --project backend --extra hosting --extra aliyun --extra adk --extra litellm
COPY --from=frontend /build/frontend/dist ./frontend/dist
ENV PATH="/opt/oaw/backend/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    OPEN_AGENT_WORLD_DATA_ROOT=/var/lib/oaw
RUN mkdir -p /var/lib/oaw && chown oaw:oaw /var/lib/oaw
USER oaw
EXPOSE 8080
CMD ["uvicorn", "backend.cloud.aliyun.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8080", "--workers", "1", "--no-proxy-headers"]
