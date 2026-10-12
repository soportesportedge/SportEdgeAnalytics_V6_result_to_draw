ARG BASE_IMAGE=sportedge-analytics-v6:1.1.0
FROM ${BASE_IMAGE}
USER root
WORKDIR /opt/result_to_draw
COPY scripts/model_core.py scripts/feature_engineering.py ./
COPY service/app.py ./app.py
COPY assets ./assets
RUN mkdir -p /data && chown 10001:10001 /data
USER 10001:10001
ENV PYTHONPATH=/opt/result_to_draw MODEL_ASSETS=/opt/result_to_draw/assets RESULTS_DB=/data/result_to_draw.sqlite OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1
EXPOSE 8788
CMD ["python", "-m", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8788", "--workers", "1"]
