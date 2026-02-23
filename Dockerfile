FROM python:3.14-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir --no-deps pykrx==1.2.4 && \
    pip install --no-cache-dir $(grep -v '^pykrx' requirements.txt)

COPY . .

EXPOSE 5000

CMD ["python", "run.py"]
