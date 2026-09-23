# LayaWatch image: stage 1 builds the Next.js static export, stage 2 serves it.
# Engine wheels are a BUILD-time option: ARG WITH_ENGINE=1 (default) installs torch + laya so
# one image deploys the real engine; console-only builds pass --build-arg WITH_ENGINE=0 and get
# the wheel-less image. The engine stays optional at RUNTIME as a code property: without it
# python -m layawatch boots the lazy fake adapter (layawatch/__main__.py).

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

# Engine wheels. Declared before any app code is copied, so this layer is keyed only on the
# pins below: layawatch/pyproject edits never re-download torch/laya. WITH_ENGINE=0 skips the
# install (console-only build, today's wheel-less behavior). Pins mirror the host venv:
# laya's declared deps (laya-0.3.5.dist-info/METADATA) at host-resolved versions; torch is the
# CPU wheel (+cpu) from download.pytorch.org, matching the host install. The import check is
# the host smoke line: .venv/bin/python -c "import torch, laya".
ARG WITH_ENGINE=1
RUN if [ "$WITH_ENGINE" = "1" ]; then \
        pip install --no-cache-dir \
            --extra-index-url https://download.pytorch.org/whl/cpu \
            torch==2.14.0+cpu \
            laya==0.3.5 \
            transformers==5.17.0 \
            safetensors==0.8.0 \
            huggingface-hub==1.32.0 \
            numpy==2.5.3 \
        && python -c "import torch, laya"; \
    fi

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
