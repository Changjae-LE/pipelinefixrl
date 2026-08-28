# syntax=docker/dockerfile:1
# Build context is the repository root.

FROM python:3.12-slim AS build
ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
RUN python -m venv /venv
ENV PATH="/venv/bin:$PATH"
COPY requirements.txt ./
RUN pip install -r requirements.txt

FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/venv/bin:$PATH" \
    APP_VERSION=0.1.0
# Non-root runtime user; compatible with securityContext.runAsNonRoot / runAsUser 1000.
RUN groupadd -g 1000 app \
    && useradd -u 1000 -g 1000 -M -s /usr/sbin/nologin app
COPY --from=build /venv /venv
WORKDIR /app
COPY app ./app
USER 1000
EXPOSE 8000
# Probe logic lives in the Helm chart, not here, so scenarios can patch it.
ENTRYPOINT ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
