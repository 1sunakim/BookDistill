ARG BASE_IMAGE=python:3.11-slim
FROM ${BASE_IMAGE}

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
RUN pip install --no-cache-dir uv==0.12.1
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable \
    && useradd --system --home /app agent \
    && mkdir -p /app/.runtime \
    && chown -R agent:agent /app/.runtime
ENV PATH="/app/.venv/bin:${PATH}" AGENT_DB_PATH=/app/.runtime/agent-runs.sqlite3
USER agent
EXPOSE 8081
CMD ["bookdistill-agent-api"]
