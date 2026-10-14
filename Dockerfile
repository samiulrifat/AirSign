# Stage 1: build the web app
FROM node:22-slim AS web
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# Stage 2: the API, serving the built web app
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY backend/requirements.txt backend/requirements.txt
RUN pip install -r backend/requirements.txt
COPY backend/ backend/
COPY --from=web /app/frontend/dist frontend/dist
RUN useradd --create-home airsign && mkdir -p data && chown airsign data
USER airsign
EXPOSE 8000
# Render (and most hosts) pass the port in $PORT.
CMD ["sh", "-c", "uvicorn airsign.main:app --app-dir backend --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips='*'"]
