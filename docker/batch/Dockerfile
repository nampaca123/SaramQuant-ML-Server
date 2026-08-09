# 배치(Fargate)용 이미지 — pykrx는 낡은 핀 의존성을 끌어오지 않도록 --no-deps로 먼저 설치한다.
FROM python:3.14-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir --no-deps pykrx==1.2.4 && \
    pip install --no-cache-dir $(grep -v '^pykrx' requirements.txt)

COPY app/ ./app/

ENTRYPOINT ["python", "-m", "app.pipeline"]
