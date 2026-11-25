# syntax=docker/dockerfile:1.7
#
# Image multi-stage : uv construit le virtualenv, l'image finale ne contient que Python slim,
# le venv et la config par défaut. Utilisateur non-root, pas de HEALTHCHECK (c'est un job).

ARG PYTHON_VERSION=3.12
ARG UV_VERSION=0.13.10

FROM ghcr.io/astral-sh/uv:${UV_VERSION} AS uv

# --------------------------------------------------------------------------- build
FROM python:${PYTHON_VERSION}-slim-trixie AS builder

COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv

WORKDIR /src

# 1) dépendances seules : couche en cache tant que uv.lock ne bouge pas
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --frozen --no-dev --no-install-project

# 2) le projet lui-même, installé en mode non éditable dans le venv
COPY pyproject.toml uv.lock README.md LICENSE ./
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable

# --------------------------------------------------------------------------- runtime
FROM python:${PYTHON_VERSION}-slim-trixie AS runtime

ARG VERSION=dev
LABEL org.opencontainers.image.title="jobwatch" \
      org.opencontainers.image.description="Veille quotidienne des offres France Travail (CDI DevOps / Cloud / Run)" \
      org.opencontainers.image.source="https://github.com/CalixteManaud/jobwatch" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.version="${VERSION}"

RUN groupadd --system --gid 10001 jobwatch \
 && useradd --system --uid 10001 --gid jobwatch --home-dir /app --no-create-home \
      --shell /usr/sbin/nologin jobwatch \
 && mkdir -p /data/state /data/out \
 && chown -R jobwatch:jobwatch /data

ENV PATH="/opt/venv/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    CONFIG_PATH=/app/config.yaml \
    STATE_PATH=/data/state/jobwatch.db \
    OUT_DIR=/data/out

WORKDIR /app
COPY --from=builder /opt/venv /opt/venv
COPY config.yaml /app/config.yaml

USER 10001:10001
ENTRYPOINT ["jobwatch"]
CMD ["run"]
