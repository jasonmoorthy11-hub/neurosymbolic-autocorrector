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

# Install the package with all dependencies
RUN pip install --no-cache-dir -e ".[all]"

# --- Production image ---
FROM base AS production

COPY scripts/ scripts/
COPY tests/ tests/

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Default: run the dashboard API
CMD ["uvicorn", "autocorrector.dashboard.api:app", "--host", "0.0.0.0", "--port", "8000"]
