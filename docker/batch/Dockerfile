# 배치(Fargate)용 이미지 — 루트 Dockerfile 기반, 진입점만 파이프라인으로 교체
FROM python:3.14-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir --no-deps pykrx==1.2.4 && \
    pip install --no-cache-dir $(grep -v '^pykrx' requirements.txt)

COPY . .

ENTRYPOINT ["python", "-m", "app.pipeline"]
