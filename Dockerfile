# Single image: the React dashboard is built, then served by the FastAPI app on the same origin.

FROM node:22-slim AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 KC_STATIC_DIR=/srv/static KC_AGENT_DIR=/srv/agent
WORKDIR /srv

COPY server/pyproject.toml ./
COPY server/app ./app
RUN pip install --no-cache-dir .

COPY server/alembic.ini ./
COPY server/migrations ./migrations
COPY --from=web /web/dist ./static
# Windows agent files, served inside the dashboard's one-click installer download.
COPY agent ./agent

RUN useradd --system --uid 10001 api
USER api

EXPOSE 8000
# Apply migrations, then serve. TLS is terminated by the host (Render) in front of this container.
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips='*'"]
