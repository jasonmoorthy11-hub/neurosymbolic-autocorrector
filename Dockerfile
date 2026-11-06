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
ENV FTLANG_CACHE=/app/.cache/fast_langdetect
RUN python -c "from autocorrector.multilingual.detector import detect_language; \
    print(detect_language('Warm up the language detection model before the first request.'))"

# --- Production image ---
FROM base AS production

COPY scripts/ scripts/
COPY tests/ tests/

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Default: run the dashboard API
CMD ["uvicorn", "autocorrector.dashboard.api:app", "--host", "0.0.0.0", "--port", "8000"]
