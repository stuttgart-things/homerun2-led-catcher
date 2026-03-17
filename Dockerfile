FROM python:3.12-slim AS base

WORKDIR /app

COPY pyproject.toml ./
COPY src/ src/

RUN pip install --no-cache-dir . \
    && rm -rf /root/.cache

# Build args for version info
ARG VERSION=dev
ARG COMMIT=unknown
ARG DATE=unknown
ENV VERSION=${VERSION} COMMIT=${COMMIT} DATE=${DATE}

# Copy assets
COPY fonts/ fonts/
COPY visual_aid/ visual_aid/

# Non-root user
RUN useradd --uid 65532 --no-create-home appuser
USER 65532

EXPOSE 8080

ENTRYPOINT ["led-catcher"]
