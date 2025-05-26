# Multi-stage build for optimized image size
FROM python:3.10-slim AS builder

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    make \
    cmake \
    libffi-dev \
    libssl-dev \
    python3-dev \
    && rm -rf /var/lib/apt/lists/*

# Create virtual environment
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Copy requirements first for better caching
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Final stage - optimized for Raspberry Pi
FROM python:3.10-slim

# Install runtime dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN groupadd -r gmail && useradd -r -g gmail gmail

# Copy virtual environment from builder
COPY --from=builder /opt/venv /opt/venv

# Set working directory
WORKDIR /app

# Copy application files
COPY --chown=gmail:gmail . /app/

# Create necessary directories
RUN mkdir -p /app/data /app/logs /app/config && \
    chown -R gmail:gmail /app

# Set environment variables
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONPATH=/app \
    PYTHONUNBUFFERED=1 \
    FLASK_APP=app.py \
    TRANSFORMERS_CACHE=/tmp/transformers_cache \
    HF_HOME=/tmp/huggingface

# Create cache directories with proper permissions
RUN mkdir -p /tmp/transformers_cache /tmp/huggingface && \
    chown -R gmail:gmail /tmp/transformers_cache /tmp/huggingface

# Switch to non-root user
USER gmail

# Expose port
EXPOSE 5000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD curl -f http://localhost:5000/health || exit 1

# Run the application
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "2", "--threads", "4", "--timeout", "120", "--access-logfile", "-", "--error-logfile", "-", "app:app"]
