# syntax=docker/dockerfile:1

# ---- stage 1: build the React dashboard ----
FROM node:20-slim AS dashboard-build
WORKDIR /dashboard
COPY dashboard/package.json dashboard/package-lock.json ./
# npm ci: installs exactly what the lockfile says (faster than npm install,
# which re-resolves). The cache mount keeps downloaded packages between
# builds, so a changed package.json only fetches what changed.
RUN --mount=type=cache,target=/root/.npm npm ci
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
# The cache mounts keep apt's downloads between builds; the docker-clean
# hook is removed because it would delete them again. Nothing is left in
# the image layer, so no `rm -rf /var/lib/apt/lists` is needed either.
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    rm -f /etc/apt/apt.conf.d/docker-clean \
    && apt-get update && apt-get install -y --no-install-recommends \
    libmagic1 curl ca-certificates pandoc \
    libpango-1.0-0 libpangocairo-1.0-0 libcairo2 libgdk-pixbuf-2.0-0 \
    shared-mime-info fontconfig \
    fonts-crosextra-carlito fonts-dejavu-core

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# uv's download cache lives in a BuildKit cache mount (not in the image), so
# a rebuild reuses already-downloaded wheels. link-mode=copy because the
# cache and the install target are on different filesystems.
ENV UV_LINK_MODE=copy

# torch is by far the heaviest dependency (~2-3 GB with its CUDA libraries;
# the converters get a GPU, see docker-compose.yml). It gets its own layer,
# ahead of requirements.txt: editing requirements.txt then reinstalls only
# what changed instead of torch all over again. The resolver sees torch
# already satisfied when docling asks for it below.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install --system torch torchvision

COPY backend/requirements.txt .
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install --system -r requirements.txt

# A C compiler for Triton, PyTorch's GPU kernel compiler: the first time a
# Docling model runs on CUDA it JIT-builds a small C launcher and fails with
# "Failed to find C compiler" on a slim image. Placed after the pip layers
# so adding it doesn't invalidate the multi-GB torch/requirements layers.
#
# fonts-noto-core: glyphs for Devanagari (Hindi), Arabic, Thai, Tamil and
# other scripts, so a PDF made by the edit feature ("translate to Hindi, as a
# PDF") doesn't render as empty boxes -- the base fonts only cover Latin.
# Chinese/Japanese/Korean would need fonts-noto-cjk (~60 MB more).
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt-get update && apt-get install -y --no-install-recommends gcc libc6-dev fonts-noto-core

COPY backend/app ./app
COPY --from=dashboard-build /dashboard/dist /app/dashboard_dist

ENV PYTHONUNBUFFERED=1
ENV DASHBOARD_DIST_DIR=/app/dashboard_dist

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
