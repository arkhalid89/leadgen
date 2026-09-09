# --- stage 1: build the Next.js frontend -----------------------------------
FROM node:22-slim AS web

WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci

COPY web/ ./
# Baked into the client bundle at build time; override for a real deployment.
ARG NEXT_PUBLIC_API_URL=http://localhost:8000
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL
RUN npm run build


# --- stage 2: the API, with Chromium for scraping ---------------------------
FROM python:3.12-slim AS api

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    CHROME_BIN=/usr/bin/chromium \
    CHROMEDRIVER_PATH=/usr/bin/chromedriver

# Chromium plus the shared libraries headless Chrome still links against.
RUN apt-get update && apt-get install -y --no-install-recommends \
        chromium chromium-driver \
        fonts-liberation libnss3 libxss1 libasound2 libgbm1 \
        ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY server/ ./server/
COPY scripts/ ./scripts/
COPY run.py .

RUN mkdir -p /app/data /app/output && useradd -m -u 1000 leadgen \
    && chown -R leadgen:leadgen /app
USER leadgen

ENV LEADGEN_DB_PATH=/app/data/leadgen.db \
    LEADGEN_OUTPUT_DIR=/app/output \
    LEADGEN_ENV=production \
    LEADGEN_HOST=0.0.0.0 \
    LEADGEN_PORT=8000

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://localhost:8000/health || exit 1

CMD ["python", "run.py"]


# --- stage 3: the frontend runtime -----------------------------------------
FROM node:22-slim AS frontend

WORKDIR /web
ENV NODE_ENV=production

COPY --from=web /web/.next ./.next
COPY --from=web /web/public ./public
COPY --from=web /web/node_modules ./node_modules
COPY --from=web /web/package.json ./

EXPOSE 3600
CMD ["npm", "run", "start"]
