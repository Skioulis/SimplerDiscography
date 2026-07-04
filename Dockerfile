FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    FLASK_APP=app \
    DISCOGRAPHY_DB=/data/db/discography.db \
    MEDIA_DIR=/data/media

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Mount points for the two persistent volumes (database + media files).
RUN mkdir -p /data/db /data/media && chmod +x docker/entrypoint.sh

EXPOSE 5000

ENTRYPOINT ["/app/docker/entrypoint.sh"]
