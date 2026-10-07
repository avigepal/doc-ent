# syntax=docker/dockerfile:1

# ---- stage 1: build the React dashboard ----
FROM node:20-slim AS dashboard-build
WORKDIR /dashboard
COPY dashboard/package.json dashboard/package-lock.json* ./
RUN npm install
COPY dashboard/ ./
RUN npm run build

# ---- stage 2: the API image ----
FROM python:3.11-slim

# texlive-xetex pulls a few hundred MB (full LaTeX engine) but is the
# reliable apt-installable path for pandoc's PDF export (xelatex) — see
# app/export/pandoc_export.py's _PDF_ENGINE. Confirmed via live testing
# that /export 500s without this: pandoc was missing entirely before.
# fonts-crosextra-carlito/fonts-dejavu-core give the report template
# (app/export/templates/report.latex) a modern sans body font instead of
# LaTeX's default Computer Modern look.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libmagic1 curl ca-certificates pandoc texlive-xetex \
    fonts-crosextra-carlito fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

COPY backend/requirements.txt .
RUN uv pip install --system --no-cache -r requirements.txt

COPY backend/app ./app
COPY --from=dashboard-build /dashboard/dist /app/dashboard_dist

ENV PYTHONUNBUFFERED=1
ENV DASHBOARD_DIST_DIR=/app/dashboard_dist

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
