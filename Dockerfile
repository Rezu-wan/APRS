FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Dependencies first for layer caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Project code (models/ ships the pre-trained .joblib artifacts in the image)
COPY alembic.ini .
COPY api/ ./api/
COPY ml/ ./ml/
COPY models/ ./models/

# Non-root user
RUN useradd --create-home --uid 1000 appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

# /health returns 200 once the app is up (no DB write). start_period covers
# the alembic migration that runs before uvicorn exec's in.
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4)" || exit 1

CMD ["sh", "-c", "python -m alembic upgrade head && exec uvicorn api.main:app --host 0.0.0.0 --port 8000"]
