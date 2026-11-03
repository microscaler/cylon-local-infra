# TimesFM forecast API for the cylon ts-inference node (spark3, DGX Spark GB10, arm64).
# torch from the CUDA 13.0 wheel index first so timesfm never pulls a CPU torch.
FROM python:3.12-slim
ARG TIMESFM_REF=master
ENV PIP_NO_CACHE_DIR=1 PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 HF_HOME=/hf
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/* \
 && pip install --index-url https://download.pytorch.org/whl/cu130 torch \
 && pip install "timesfm[torch] @ git+https://github.com/google-research/timesfm.git@${TIMESFM_REF}" \
 && python -c "import timesfm, torch; print('timesfm', getattr(timesfm,'__version__','?'), 'torch', torch.__version__)"
COPY server.py /opt/timesfm/server.py
EXPOSE 8080
CMD ["python", "/opt/timesfm/server.py"]
