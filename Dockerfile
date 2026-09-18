FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
COPY alembic.ini ./
COPY migrations ./migrations
RUN pip install --no-cache-dir ".[postgres,qdrant,models]"
RUN mkdir -p /app/data
EXPOSE 8000
CMD ["uvicorn", "bookdistill.main:app", "--host", "0.0.0.0", "--port", "8000"]
