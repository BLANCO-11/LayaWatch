# LayaWatch image: stage 1 builds the Next.js static export, stage 2 serves it.
# The engine (laya + torch) is optional at runtime: without it python -m layawatch boots the
# lazy fake adapter (layawatch/__main__.py), so this image needs no engine wheels.

# --- stage 1: web build (only web/ files enter this stage; Python edits never bust this cache)
FROM node:24-slim AS web
WORKDIR /build
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
RUN npm run build

# --- stage 2: runtime (python:3.12-slim, no Node, non-root) ---
FROM python:3.12-slim

# LAYA_STATE_DIR and LAYWATCH_BIND map to layawatch/config.py; HF_HUB_CACHE is the path
# engine/adapter.py:_hf_cache_root() consults for checkpoint downloads; HOME keeps XDG/torch
# caches inside the writable /data volume under a read-only root filesystem.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    LAYA_STATE_DIR=/data \
    LAYWATCH_BIND=0.0.0.0 \
    HF_HUB_CACHE=/models \
    HOME=/data

WORKDIR /app

# pyproject.toml is the dependency authority (fastapi/uvicorn/httpx only). Copying the package
# into /build (not the workdir) keeps site-packages the single import source.
COPY pyproject.toml /build/pyproject.toml
COPY layawatch /build/layawatch
RUN pip install --no-cache-dir /build && rm -rf /build

# Password deny list: auth/passwords.py resolves the repository-root data/ dir as
# parents[2] of the installed auth/passwords.py, i.e. site-packages/data/ in an image.
COPY data/common_passwords.txt /tmp/common_passwords.txt
RUN SITE="$(python -c 'import site; print(site.getsitepackages()[0])')" \
    && mkdir -p "$SITE/data" \
    && mv /tmp/common_passwords.txt "$SITE/data/common_passwords.txt"

# Static export served from the working directory (config.web_root default is web/out).
COPY --from=web /build/out /app/web/out

# uid 10001 per the phase-8 contract; only /data (state) and /models (HF cache) are writable.
RUN useradd --uid 10001 --user-group --home-dir /app --no-create-home \
        --shell /usr/sbin/nologin layawatch \
    && mkdir -p /data /models \
    && chown layawatch:layawatch /data /models

USER layawatch

EXPOSE 8050

# python:3.12-slim ships neither curl nor wget: poll /healthz with the stdlib instead.
# The start period covers first-boot migrations and checkpoint warmup; /healthz answers
# before any model loads, a failure raises (non-zero exit) on connection or HTTP errors.
HEALTHCHECK --interval=30s --timeout=5s --start-period=90s --retries=3 \
    CMD python -c "import os, urllib.request as u; \
        u.urlopen('http://127.0.0.1:%s/healthz' % os.environ.get('LAYA_PORT', '8050'), \
                  timeout=4)"

CMD ["python", "-m", "layawatch"]
