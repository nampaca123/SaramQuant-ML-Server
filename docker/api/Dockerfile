# 서빙(Lambda)용 이미지 — pykrx는 낡은 핀 의존성을 끌어오지 않도록 --no-deps로 설치한다.
FROM public.ecr.aws/lambda/python:3.12

ENV DUCKDB_EXTENSION_DIR=/opt/duckdb-extensions

COPY requirements-api.txt ${LAMBDA_TASK_ROOT}/
RUN pip install --no-cache-dir -r ${LAMBDA_TASK_ROOT}/requirements-api.txt && \
    pip install --no-cache-dir --no-deps pykrx==1.2.4

RUN python -c "import duckdb; c = duckdb.connect(config={'extension_directory': '/opt/duckdb-extensions'}); [c.execute(s) for s in ('INSTALL httpfs', 'INSTALL avro', 'INSTALL iceberg')]"

# 오프라인 LOAD 검증 — 확장 베이크가 깨지면 여기서 빌드를 실패시킨다(런타임 설치는 불가).
RUN python -c "import duckdb; c = duckdb.connect(config={'extension_directory': '/opt/duckdb-extensions'}); [c.execute(s) for s in ('SET autoinstall_known_extensions=false', 'SET autoload_known_extensions=false', 'LOAD httpfs', 'LOAD iceberg')]"

COPY app/ ${LAMBDA_TASK_ROOT}/app/

CMD ["app.api.lambda_handlers.handle_health"]
