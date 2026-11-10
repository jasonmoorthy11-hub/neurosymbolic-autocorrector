# Multi-stage build for the Neurosymbolic Autocorrector
FROM python:3.12-slim AS base

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy dependency files first (cache layer)
COPY pyproject.toml .
COPY src/ src/

# Install the package with all dependencies. A slow route to PyPI must not fail
# the build (pip's default read timeout is 15 s), and the BuildKit cache mount
# keeps downloaded wheels across retries and rebuilds without bloating the image.
ENV PIP_DEFAULT_TIMEOUT=120 PIP_RETRIES=10
RUN --mount=type=cache,target=/root/.cache/pip pip install -e ".[all]"

# Fetch the fastText language model now: otherwise the first request downloads
# 125 MB inside the container and the API answers nothing until it is done.
# The download resumes across retries and rebuilds (cache mount), and the
# checksum is the one fast-langdetect verifies when it loads the file.
ENV FTLANG_CACHE=/app/.cache/fast_langdetect
ARG FTLANG_MODEL_URL=https://dl.fbaipublicfiles.com/fasttext/supervised-models/lid.176.bin
ARG FTLANG_MODEL_MD5=01810bc59c6a3d2b79c79e6336612f65
RUN --mount=type=cache,target=/var/cache/ftlang \
    cd /var/cache/ftlang \
    && (echo "$FTLANG_MODEL_MD5  lid.176.bin" | md5sum -c - \
        || curl -fL --retry 20 --retry-all-errors --retry-max-time 900 \
            --speed-limit 1024 --speed-time 60 \
            -C - -o lid.176.bin "$FTLANG_MODEL_URL") \
    && (echo "$FTLANG_MODEL_MD5  lid.176.bin" | md5sum -c - \
        || { rm -f lid.176.bin; echo "lid.176.bin failed its checksum; removed, rebuild to retry"; exit 1; }) \
    && mkdir -p "$FTLANG_CACHE" && cp lid.176.bin "$FTLANG_CACHE/lid.176.bin"
# detect_language() answers "en" when the model is missing or cannot be loaded,
# so the warm-up sentence is French and the build fails unless the answer is
# "fr"; the file must also still be there afterwards (fast-langdetect deletes a
# model that fails its checksum).
RUN python -c "import sys; from autocorrector.multilingual.detector import detect_language; \
    lang = detect_language('Ceci est une phrase en francais pour verifier que le modele de langue est bien charge.'); \
    print(lang); sys.exit(lang != 'fr')" \
    && test -s "$FTLANG_CACHE/lid.176.bin"

# --- Production image ---
FROM base AS production

# The API and the seed service need src/ (installed above) and scripts/ only
COPY scripts/ scripts/

# Run unprivileged. The user owns the language-model cache directory and the
# audit directory; a new named volume mounted there starts with that owner.
ENV AUTOCORRECTOR_AUDIT_DB_PATH=/var/lib/autocorrector/audit.db
RUN useradd --uid 10001 --create-home app \
    && mkdir -p /var/lib/autocorrector \
    && chown app:app /app/.cache /app/.cache/fast_langdetect /var/lib/autocorrector
USER app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Default: run the dashboard API. 0.0.0.0 is required inside a container; what
# reaches it from outside is decided by the published port (loopback in compose).
CMD ["uvicorn", "autocorrector.dashboard.api:app", "--host", "0.0.0.0", "--port", "8000"]
