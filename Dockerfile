FROM python:3.14.7-slim-trixie AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /build

COPY pyproject.toml README.md ./
COPY src ./src
COPY packages/osm-manager ./packages/osm-manager
COPY packages/osm-routing ./packages/osm-routing
COPY web/pyproject.toml ./web/pyproject.toml
COPY web/backend ./web/backend

RUN python -m pip wheel --wheel-dir /wheels \
      pyvalhalla==3.8.3 osmium==4.3.0 \
      . ./packages/osm-manager ./packages/osm-routing ./web


FROM python:3.14.7-slim-trixie AS runtime

ARG APP_UID=10001
ARG APP_GID=10001

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    WARPBUSTER_WEB_DATA=/data \
    WARPBUSTER_WEB_STATIC=/app/web/dist \
    WARPBUSTER_WEB_OSM_MODE=auto \
    WARPBUSTER_WEB_PORT=8080

LABEL org.opencontainers.image.title="WarpBuster Web" \
      org.opencontainers.image.source="https://github.com/zloynemec/warpbuster"

RUN apt-get update \
    && apt-get install --yes --no-install-recommends libexpat1 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid "${APP_GID}" warpbuster \
    && useradd --uid "${APP_UID}" --gid "${APP_GID}" --no-create-home --shell /usr/sbin/nologin warpbuster \
    && install -d -o "${APP_UID}" -g "${APP_GID}" -m 0700 /data /app/web

COPY --from=builder /wheels /wheels
RUN python -m pip install --no-index --find-links=/wheels warpbuster-web==0.1.0 \
    && rm -rf /wheels

COPY --chown=${APP_UID}:${APP_GID} web/dist /app/web/dist

USER ${APP_UID}:${APP_GID}
WORKDIR /app

EXPOSE 8080

HEALTHCHECK --interval=20s --timeout=4s --start-period=15s --retries=3 \
    CMD ["python", "-m", "warpbuster_web.healthcheck"]

CMD ["python", "-m", "warpbuster_web", "--host", "0.0.0.0", "--port", "8080"]
