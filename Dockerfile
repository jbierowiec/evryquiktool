FROM python:3.12-slim

# 1) System deps (ffmpeg)
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg \
 && rm -rf /var/lib/apt/lists/*

# 2) App deps
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 3) App code
COPY . .

ENV PYTHONUNBUFFERED=1
CMD ["gunicorn","app:app","--bind","0.0.0.0:${PORT}","--workers","2","--threads","4","--timeout","120"]
