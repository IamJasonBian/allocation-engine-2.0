# Risk service on Cloud Run: the Flask API only (/api/pnl/risk*, /api/health).
# No engine loop — ENGINE_ENABLED=false — Robinhood/Alpaca execution stays on
# the Render worker. Build context is the repo root (see cloudbuild.yaml).
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PYTHONUNBUFFERED=1 \
    ENGINE_ENABLED=false \
    DRY_RUN=true

# Cloud Run injects $PORT; gunicorn.conf.py binds to it.
CMD ["gunicorn", "-c", "gunicorn.conf.py", "app.wsgi:application"]
