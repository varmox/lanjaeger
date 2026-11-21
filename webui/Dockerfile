# lanjaeger web UI: Angular frontend + Python API in one small image.
# Based on docker:cli so the API can run `docker compose` for updates.

FROM node:24-alpine AS frontend
WORKDIR /src
# Direct dependencies are pinned exactly in package.json. Switch to
# `npm ci` once frontend/package-lock.json is committed.
COPY frontend/package*.json ./
RUN if [ -f package-lock.json ]; then npm ci --no-audit --no-fund; else npm install --no-audit --no-fund; fi
COPY frontend/ ./
RUN npm run build

FROM docker:29-cli
RUN apk add --no-cache python3 tzdata
WORKDIR /app
COPY server/lanjaeger_webui ./lanjaeger_webui
COPY --from=frontend /src/dist ./static
ENV PYTHONUNBUFFERED=1 STATIC_DIR=/app/static PORT=8080
EXPOSE 8080
HEALTHCHECK --interval=1m --timeout=5s --start-period=20s \
  CMD wget -qO- http://127.0.0.1:8080/api/session >/dev/null || exit 1
CMD ["python3", "-m", "lanjaeger_webui.app"]
