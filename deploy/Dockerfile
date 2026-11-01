FROM python:3.14-slim@sha256:cd89c30eda26ee911a4611b18680086e0ea42e6f5f8cbe653d07fa7958c9cec8
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
COPY requirements-service.lock /app/
RUN pip install --no-cache-dir --require-hashes -r requirements-service.lock \
    && groupadd --gid 10001 bemix \
    && useradd --uid 10001 --gid 10001 --no-create-home bemix \
    && mkdir -p /var/lib/bemix /run/bemix-keys \
    && chown 10001:10001 /var/lib/bemix /run/bemix-keys \
    && chmod 700 /var/lib/bemix /run/bemix-keys
COPY bemix_pilot /app/bemix_pilot
COPY bemix_service /app/bemix_service
USER 10001:10001
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "bemix_service.api:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--no-proxy-headers", "--no-access-log", "--limit-concurrency", "32", "--backlog", "128", "--timeout-keep-alive", "5", "--timeout-graceful-shutdown", "30"]
