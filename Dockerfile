FROM node:22-alpine AS frontend
RUN apk add --no-cache libc6-compat
WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend ./
ENV NEXT_OUTPUT=export
RUN npm run build

FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DATABASE_PATH=/data/mood.sqlite3 \
    DOCS_DIR=/data/docs

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && useradd --create-home --uid 1000 app \
    && mkdir -p /data /data/docs \
    && chown -R app:app /app /data

COPY --chown=app:app . .
COPY --from=frontend --chown=app:app /frontend/out /app/frontend/out

USER app

EXPOSE 8000

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
