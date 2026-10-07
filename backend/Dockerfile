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

# PDF export is pandoc (markdown->HTML) + weasyprint (HTML+CSS->PDF) — see
# app/export/pandoc_export.py and app/export/templates/report.html. This
# replaced a texlive-xetex/LaTeX pipeline that alone took ~29 minutes to
# apt-install; weasyprint's system libs below are a fraction of that.
# fonts-crosextra-carlito/fonts-dejavu-core give the report a modern sans
# body font, reused by both the PDF (report.html) and docx
# (reference.docx) templates.
# libgdk-pixbuf-2.0-0 (dash before "2.0"): Debian renamed this package in
# trixie from the older libgdk-pixbuf2.0-0 name used on bookworm/older.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libmagic1 curl ca-certificates pandoc \
    libpango-1.0-0 libpangocairo-1.0-0 libcairo2 libgdk-pixbuf-2.0-0 \
    shared-mime-info fontconfig \
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
