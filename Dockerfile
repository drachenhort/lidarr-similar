FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY lidarr_similar ./lidarr_similar

ENV STORE_PATH=/data/lidarr_similar_store.sqlite3
ENV CACHE_PATH=/data/lidarr_similar.sqlite3
VOLUME ["/data"]

EXPOSE 8000

CMD ["uvicorn", "lidarr_similar.web:app", "--host", "0.0.0.0", "--port", "8000"]
