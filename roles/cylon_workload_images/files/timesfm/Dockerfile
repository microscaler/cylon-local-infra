# TimesFM forecast API for the cylon ts-inference node (spark3, DGX Spark GB10, arm64).
# torch from the CUDA 13.0 wheel index first so timesfm never pulls a CPU torch.
# BASE may be any local image that already holds the same torch (cylon_workload_torch_base):
# the torch step is then a no-op instead of a ~4 GB download.
ARG BASE=python:3.12-slim
FROM ${BASE}
ARG TIMESFM_REF=master
ENV PIP_NO_CACHE_DIR=1 PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 HF_HOME=/hf
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/* \
 && (python -c "import torch" 2>/dev/null || pip install --index-url https://download.pytorch.org/whl/cu130 torch) \
 && pip install "timesfm[torch] @ git+https://github.com/google-research/timesfm.git@${TIMESFM_REF}" \
 && python -c "import timesfm, torch; print('timesfm', getattr(timesfm,'__version__','?'), 'torch', torch.__version__)"
COPY server.py /opt/timesfm/server.py
EXPOSE 8080
CMD ["python", "/opt/timesfm/server.py"]
