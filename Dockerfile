FROM python:3.12-slim

# System dependencies
RUN apt-get update && \
    apt-get install -y --no-recommends build-essential cmake curl && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Python dependencies
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt && rm /tmp/requirements.txt

# Application code and data directories
COPY . /app
RUN mkdir -p /data /models
VOLUME /models

# Environment defaults
ENV MODEL_PATH=/models/model.gguf \
    USE_DB_QUEUE=true \
    DB_PATH=/data/queue.db \
    OLLAMA_URL=http://ollama:11434

CMD ["uvicorn", "app.server:app", "--host", "0.0.0.0", "--port", "8000"]
