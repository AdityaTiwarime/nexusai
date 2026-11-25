# syntax=docker/dockerfile:1
FROM python:3.13-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Take the latest Debian security fixes, not only what the base image shipped with.
RUN apt-get update \
    && apt-get upgrade -y --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*

# Install dependencies first so code changes don't invalidate this layer. pip itself is
# removed afterwards: the app doesn't need it at runtime, and an old pip carries its own
# vendored copies of libraries such as urllib3 that image scanners rightly flag.
COPY pyproject.toml ./
RUN pip install --upgrade pip \
    && mkdir app && touch app/__init__.py && pip install . && rm -rf app \
    && pip uninstall -y pip

COPY alembic.ini ./
COPY alembic ./alembic
COPY app ./app

# Run as an unprivileged user. /data/uploads is owned by it, so a fresh Docker
# volume mounted there is writable too.
RUN useradd --create-home --uid 10001 nexus \
    && mkdir -p /data/uploads && chown -R nexus:nexus /data
USER nexus

EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=3s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/api/v1/health/live', timeout=2).status == 200 else 1)"

# Apply migrations, then start the API. (The worker overrides this: `python -m app.worker`.)
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.asgi:app --host 0.0.0.0 --port 8000 --proxy-headers"]
