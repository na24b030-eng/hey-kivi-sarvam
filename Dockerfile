# Build Kivi's browser client first, then serve it from the FastAPI process.
FROM node:24-bookworm-slim AS frontend-build
WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8000
WORKDIR /app

COPY backend/ ./backend/
RUN pip install --no-cache-dir ./backend
COPY --from=frontend-build /build/frontend/dist ./frontend/dist

# Railway supplies PORT. APP_DATA_DIR must be set to the mounted volume path
# in the Railway dashboard so SQLite, its WAL file, and cached embeddings persist.
EXPOSE 8000
CMD ["/bin/sh", "-c", "cd /app/backend && python -m kivi_memory.cli migrate && exec uvicorn kivi_memory.api:create_app --factory --host 0.0.0.0 --port ${PORT:-8000}"]
