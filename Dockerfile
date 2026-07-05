FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY *.py .
COPY dashboard.html .
COPY companion_frontend ./companion_frontend
COPY config.example.yaml ./config.yaml

RUN mkdir -p /app/buckets && chmod -R 777 /app/buckets

ENV OMBRE_TRANSPORT=streamable-http
ENV OMBRE_BUCKETS_DIR=/app/buckets
ENV OMBRE_PORT=8000

VOLUME ["/app/buckets"]
EXPOSE 8000

CMD ["python", "zeabur_gateway_bootstrap.py"]
