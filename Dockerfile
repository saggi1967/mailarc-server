# mailarc-server — schlankes Runtime-Image
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Erst Metadaten + Code kopieren, dann installieren (inkl. Postgres-Treiber).
COPY pyproject.toml ./
COPY app ./app
RUN pip install ".[postgres]"

EXPOSE 8000

# Health über die Bordmittel des Images (kein curl im slim-Image nötig).
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/health').status==200 else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
