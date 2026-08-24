# syntax=docker/dockerfile:1.7

FROM python:3.12.14-slim-bookworm AS wheels

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /build
COPY requirements-lock.txt ./
RUN python -m pip wheel --wheel-dir=/wheels --requirement requirements-lock.txt


FROM python:3.12.14-slim-bookworm AS runtime

ARG APP_UID=10001
ARG APP_GID=10001
ARG APP_BUILD_ID=local-development
ARG SOURCE_COMMIT=uncommitted

LABEL org.opencontainers.image.title="USM Scheduler" \
      org.opencontainers.image.revision="${SOURCE_COMMIT}" \
      org.opencontainers.image.version="${APP_BUILD_ID}"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DJANGO_SETTINGS_MODULE=usm_scheduler.settings \
    PORT=8000 \
    APP_BUILD_ID=${APP_BUILD_ID} \
    SOURCE_COMMIT=${SOURCE_COMMIT}

RUN groupadd --gid "${APP_GID}" scheduler \
    && useradd --uid "${APP_UID}" --gid "${APP_GID}" --create-home --shell /usr/sbin/nologin scheduler

WORKDIR /app
COPY --from=wheels /wheels /wheels
RUN python -m pip install --no-cache-dir /wheels/* \
    && rm -rf /wheels

# Explicit copies keep local secrets, research datasets, and VCS metadata outside the image.
COPY --chown=scheduler:scheduler manage.py ./
COPY --chown=scheduler:scheduler usm_scheduler ./usm_scheduler
COPY --chown=scheduler:scheduler scheduler ./scheduler
COPY --chown=scheduler:scheduler docker ./docker

RUN mkdir -p /app/media /app/staticfiles /tmp/usm-scheduler \
    && chown -R scheduler:scheduler /app/media /app/staticfiles /tmp/usm-scheduler \
    && sed -i 's/\r$//' /app/docker/entrypoint.sh \
    && chmod 0555 /app/docker/entrypoint.sh

USER scheduler
EXPOSE 8000

ENTRYPOINT ["/app/docker/entrypoint.sh"]
CMD ["gunicorn", "usm_scheduler.wsgi:application", "--bind=0.0.0.0:8000", "--workers=2", "--threads=2", "--timeout=120", "--access-logfile=-", "--error-logfile=-"]
