# Build từ thư mục gốc repo: docker build -f server/Dockerfile .
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /srv

COPY shared /srv/shared
COPY server /srv/server
RUN pip install --no-cache-dir /srv/shared /srv/server

WORKDIR /srv/server
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
