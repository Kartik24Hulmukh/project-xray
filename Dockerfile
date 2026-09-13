# ---- Stage 1: builder ----
# Official Python 3.13 Alpine image for building wheels
FROM python:3.13-alpine AS builder

WORKDIR /build

# Install build dependencies needed to compile native wheels (psycopg2, cryptography)
# These are NOT copied to the final image
RUN apk add --no-cache --virtual .build-deps \
    gcc \
    musl-dev \
    libffi-dev \
    openssl-dev \
    postgresql-dev \
    cargo

# Create a virtualenv and install requirements into it
COPY requirements.txt .
RUN python -m venv /venv \
    && /venv/bin/pip install --no-cache-dir --upgrade pip \
    && /venv/bin/pip install --no-cache-dir -r requirements.txt

# ---- Stage 2: runtime ----
# Minimal Alpine runtime — only runtime shared libraries, no compilers
FROM python:3.13-alpine

WORKDIR /app

# Install only runtime shared libraries needed by psycopg2 and cryptography
# libpq for psycopg2, libffi and openssl for cryptography
RUN apk add --no-cache \
    libpq \
    libffi \
    openssl \
    curl \
    && adduser -D -u 10001 app \
    && mkdir -p /app/data/uploads \
    && chown -R app:app /app

# Copy the virtualenv from the builder stage
COPY --from=builder /venv /venv

# Copy application source only (no tests, docs, .git, node_modules, etc.)
COPY app/ ./app/
COPY db/ ./db/
COPY schemas/ ./schemas/
COPY static/ ./static/
COPY scripts/smoke_e2e.py ./scripts/smoke_e2e.py
COPY scripts/recovery.py ./scripts/recovery.py
COPY scripts/verify_capsule.py ./scripts/verify_capsule.py
COPY scripts/recovery_evidence.py ./scripts/recovery_evidence.py
COPY requirements.txt .

# Put the venv on PATH so python3 and pip resolve to the venv
ENV PATH="/venv/bin:$PATH"
ENV PORT=8080
ENV BIND_HOST=0.0.0.0
ENV DB_PATH=/app/data/project_xray.db
ENV PYTHONUNBUFFERED=1

USER app

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=3s \
    CMD curl --fail http://127.0.0.1:8080/health || exit 1

CMD ["python3", "app/server.py"]
