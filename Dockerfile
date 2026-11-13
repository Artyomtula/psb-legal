# syntax=docker/dockerfile:1
FROM python:3.12-slim AS base

ARG WITH_LOCAL_MODELS=false
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HF_HOME=/models

WORKDIR /app
COPY requirements.txt requirements-local-models.txt ./
RUN pip install -r requirements.txt \
 && if [ "$WITH_LOCAL_MODELS" = "true" ]; then \
      pip install --extra-index-url https://download.pytorch.org/whl/cpu -r requirements-local-models.txt \
      && python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('BAAI/bge-m3')"; \
    fi

COPY legal_core ./legal_core
COPY api ./api
COPY data ./data
COPY artifacts ./artifacts

RUN useradd --create-home --uid 10001 app && chown -R app /app
USER app

EXPOSE 8000
HEALTHCHECK --interval=20s --timeout=5s --start-period=60s --retries=5 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).status == 200 else 1)"
CMD ["uvicorn", "api.server:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
