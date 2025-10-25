# ---- Build stage (optional if you have native deps) ----
FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# System deps that help manylibs (remove if unneeded)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential curl && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps first (better cache)
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of your code
COPY . /app

# Railway provides $PORT; we’ll default to 8080 locally
ENV PORT=8080 \
    WEB_CONCURRENCY=2 \
    THREADS=8 \
    TIMEOUT=30

# Expose is optional for Railway, kept for local
EXPOSE 8080

# Gunicorn with threaded worker for snappy responses
CMD gunicorn app:app \
    -k gthread \
    -w ${WEB_CONCURRENCY} \
    --threads ${THREADS} \
    -b 0.0.0.0:${PORT} \
    --timeout ${TIMEOUT} \
    --graceful-timeout 20 \
    --keep-alive 5 \
    --log-file -
