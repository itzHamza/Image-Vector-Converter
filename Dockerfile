FROM python:3.11-slim

WORKDIR /app

# System deps: vtracer's wheel is precompiled, but keep build tools around
# in case pip needs to build anything on the target architecture.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .
COPY templates/ templates/

RUN mkdir -p uploads outputs

ENV PORT=8000
EXPOSE 8000

# gunicorn for production serving
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--workers", "2", "--timeout", "120", "app:app"]
