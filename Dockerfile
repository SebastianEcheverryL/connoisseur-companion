# Container image for AWS Lambda (also runs anywhere with `docker run -p 8080:8080`).
# FastAPI runs under uvicorn; the AWS Lambda Web Adapter extension turns Lambda invocations into HTTP
# requests and streams the SSE response back through the Function URL.

ARG PYTHON_VERSION=3.12

# ---------- build: install, download the IBM data, build the vector index, cache the ONNX models
FROM python:${PYTHON_VERSION}-slim AS build
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 \
    EMBEDDING_CACHE_DIR=/opt/models CONNOISSEUR_ENV_FILE=/dev/null \
    CONNOISSEUR_DATA_DIR=/app/data CACHE_DIR=/tmp/cache
WORKDIR /app
RUN python -m venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install ".[aws]"
COPY data ./data
COPY scripts ./scripts
RUN python scripts/download_data.py --with-images \
 && python scripts/build_index.py --out /opt/chroma \
 && python scripts/warm_models.py \
 && chmod -R a+rX /opt/models /opt/chroma
# ^ huggingface_hub writes some cache files as 0600 (owner root); Lambda runs as another user and
#   Prompt Guard failed closed, blocking every web result, when it could not read them.
# fastembed 0.9 pins pillow<13, which has critical CVEs fixed in 13.0. Pillow only decodes images while
# the index above is built; the API never opens images, so the runtime gets the patched release.
RUN pip install --no-deps "pillow>=13"

# ---------- runtime: only the virtualenv, the JSON data, the index and the models
FROM python:${PYTHON_VERSION}-slim
COPY --from=public.ecr.aws/awsguru/aws-lambda-adapter:0.9.1 /lambda-adapter /opt/extensions/lambda-adapter
RUN useradd --uid 10001 --no-create-home app
COPY --from=build /opt/venv /opt/venv
COPY --from=build /opt/models /opt/models
COPY --from=build /opt/chroma /opt/chroma
COPY --from=build /app/data/*.json /app/data/*.txt /app/data/
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    HOME=/tmp \
    CONNOISSEUR_ENV_FILE=/dev/null \
    CONNOISSEUR_DATA_DIR=/app/data \
    CHROMA_SEED_DIR=/opt/chroma CHROMA_DIR=/tmp/chroma \
    EMBEDDING_CACHE_DIR=/opt/models HF_HUB_OFFLINE=1 \
    CACHE_DIR=/tmp/cache \
    ANONYMIZED_TELEMETRY=false \
    ENABLE_DEV_UI=false LOG_JSON=true ENVIRONMENT=prod \
    PORT=8080 AWS_LWA_PORT=8080 AWS_LWA_READINESS_CHECK_PATH=/health AWS_LWA_INVOKE_MODE=response_stream
WORKDIR /app
USER app
EXPOSE 8080
CMD ["uvicorn", "connoisseur.api:app", "--host", "0.0.0.0", "--port", "8080", "--proxy-headers", "--forwarded-allow-ips", "*"]
