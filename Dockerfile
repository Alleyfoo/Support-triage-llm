FROM python:3.12-slim

# System dependencies
RUN apt-get update && \
    apt-get install -y --no-recommends build-essential cmake && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /workspace

# Python dependencies
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt && rm /tmp/requirements.txt

# Application code and data directories
COPY . /workspace
RUN mkdir -p /workspace/data /models
VOLUME /models

# Environment defaults
ENV MODEL_PATH=/models/model.gguf \
    USE_DB_QUEUE=true \
    QUEUE_DB_PATH=/workspace/data/queue.db

CMD ["uvicorn", "app.server:app", "--host", "0.0.0.0", "--port", "8000"]
