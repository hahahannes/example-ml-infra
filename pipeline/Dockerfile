FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HF_HOME=/app/.cache/huggingface

WORKDIR /app

# CPU-only torch keeps the image small; drop --index-url for a CUDA build
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu \
 && pip install --no-cache-dir transformers mlflow

COPY bmw_news.py train.py split.py evaluate.py pipeline.sh ./
RUN chmod +x pipeline.sh

# Outputs (news.json, splits, model, mlruns) go here; mount a host folder to keep them
WORKDIR /app/data

CMD ["/app/pipeline.sh"]
