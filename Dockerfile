FROM python:3.12-slim

# System dependencies
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg \
 && rm -rf /var/lib/apt/lists/*

# Set work directory
WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy app source
COPY . .

# Ensure logs are flushed
ENV PYTHONUNBUFFERED=1
ENV PORT=8080

# Start Gunicorn; shell form so $PORT expands on Railway
CMD sh -c 'gunicorn app:app --bind 0.0.0.0:${PORT:-8080} --workers 2 --threads 4 --timeout 120'
