FROM python:3.12-slim

ARG WITH_LOCAL_MODELS=true
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HF_HOME=/models \
    TOKENIZERS_PARALLELISM=false

WORKDIR /app
RUN useradd --create-home --uid 10001 app \
 && mkdir -p /models /app/data /app/artifacts/index \
 && chown -R app /models /app

COPY requirements.txt requirements-local-models.txt ./
RUN pip install -r requirements.txt \
 && if [ "$WITH_LOCAL_MODELS" = "true" ]; then \
      pip install --extra-index-url https://download.pytorch.org/whl/cpu -r requirements-local-models.txt; \
    fi

USER app
RUN if [ "$WITH_LOCAL_MODELS" = "true" ]; then \
      python -c "from sentence_transformers import CrossEncoder, SentenceTransformer; SentenceTransformer('BAAI/bge-m3'); CrossEncoder('BAAI/bge-reranker-v2-m3')"; \
    fi
ENV HF_HUB_OFFLINE=1

COPY --chown=app legal_core ./legal_core
COPY --chown=app api ./api

EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s --start-period=15m --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).status == 200 else 1)"
CMD ["uvicorn", "api.server:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
