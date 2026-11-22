# Workbench only: the multimodal model runs in a separate service.
# 工作台不包含模型权重或 GPU 运行时；发布方可用已验证的镜像摘要固定基础镜像。
ARG PYTHON_IMAGE=python:3.12-slim-bookworm
FROM ${PYTHON_IMAGE}

LABEL org.opencontainers.image.title="ChineseOCR Hub" \
      org.opencontainers.image.description="Multimodal OCR and document extraction workbench" \
      org.opencontainers.image.licenses="Apache-2.0"

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 WORKBENCH_DATA_DIR=/app/data
WORKDIR /app

# Install the hashed runtime lock first so source edits reuse this layer.
# 固定 UID/GID 让命名卷及显式绑定目录有可预期的权限，运行进程不使用 root。
COPY requirements.lock ./
RUN pip install --no-cache-dir --require-hashes -r requirements.lock \
    && groupadd --gid 10001 workbench \
    && useradd --uid 10001 --gid 10001 --no-create-home workbench \
    && mkdir /app/data && chown 10001:10001 /app/data

# Copy only runtime code and licensing, never local data, .env or test fixtures.
COPY --chown=10001:10001 workbench ./workbench
COPY LICENSE NOTICE THIRD_PARTY_NOTICES.md ./
USER 10001:10001
EXPOSE 8765

# Liveness only: a healthy server does not imply that a model is configured.
HEALTHCHECK --interval=30s --timeout=3s --start-period=15s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/healthz', timeout=2)"
# The image listens inside the container; Compose publishes it on host loopback.
CMD ["python", "-m", "workbench", "--host", "0.0.0.0", "--port", "8765"]
