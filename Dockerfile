ARG BASE_IMAGE=python:3.11.9-slim-bookworm
FROM ${BASE_IMAGE}

ARG PIP_INDEX_URL=https://pypi.org/simple
ARG PIP_EXTRA_INDEX_URL=
ARG PIP_DEFAULT_TIMEOUT=100

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml ./
COPY constraints.lock ./
COPY src ./src
COPY alembic.ini ./
COPY migrations ./migrations
RUN pip install --no-cache-dir -c constraints.lock ".[postgres,qdrant,models,langchain]"
RUN groupadd --system bookdistill && useradd --system --gid bookdistill --home /app bookdistill \
    && mkdir -p /app/data /app/model-cache \
    && chown -R bookdistill:bookdistill /app
COPY --chmod=755 docker-entrypoint.sh ./
ENTRYPOINT ["/app/docker-entrypoint.sh"]
EXPOSE 8000
CMD ["uvicorn", "bookdistill.main:app", "--host", "0.0.0.0", "--port", "8000"]
