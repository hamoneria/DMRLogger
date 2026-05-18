FROM debian:bookworm-slim AS mbelib-builder

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        ca-certificates \
        cmake \
        git \
    && git clone --depth 1 https://github.com/szechyjs/mbelib.git /tmp/mbelib \
    && cmake -S /tmp/mbelib -B /tmp/mbelib/build -DCMAKE_BUILD_TYPE=Release \
    && cmake --build /tmp/mbelib/build --parallel \
    && cmake --install /tmp/mbelib/build \
    && rm -rf /tmp/mbelib /var/lib/apt/lists/*

COPY dmr_ambe33_to_wav.c /tmp/dmr_ambe33_to_wav.c
RUN gcc -O2 -Wall -Wextra -o /tmp/dmr_ambe33_to_wav /tmp/dmr_ambe33_to_wav.c -lmbe -lm

FROM python:3.11-slim-bookworm

ARG WHISPER_MODEL=base

ENV PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    LD_LIBRARY_PATH=/usr/local/lib \
    HF_HOME=/opt/huggingface \
    XDG_CACHE_HOME=/opt/cache \
    WHISPER_MODEL=${WHISPER_MODEL} \
    BM_APP_DIR=/app \
    BM_RECORDINGS_DIR=/data/recordings \
    BM_STATE_DIR=/data/state \
    BM_LOG_DIR=/data/logs \
    BM_ROUTES_CONFIG=/config/bm_direct_routes.json \
    BM_AMBE_DECODER=/app/dmr_ambe33_to_wav

ENV PATH="/opt/venv/bin:${PATH}"

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        ffmpeg \
        openssh-client \
        sshpass \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
COPY --from=mbelib-builder /usr/local/lib/libmbe* /usr/local/lib/
COPY --from=mbelib-builder /tmp/dmr_ambe33_to_wav /app/dmr_ambe33_to_wav

COPY pyproject.toml uv.lock* ./
RUN uv sync --frozen --no-dev || uv sync --no-dev

# Preload the default Faster-Whisper model into the image so runtime startup does
# not depend on downloading it from Hugging Face. Override at build time with:
#   docker build --build-arg WHISPER_MODEL=small -t dmrlogger:latest .
RUN uv run python -c "import os; from faster_whisper import WhisperModel; WhisperModel(os.environ.get('WHISPER_MODEL', 'base'), device='cpu', compute_type='int8')"

COPY . .
RUN chmod +x /app/dmr_ambe33_to_wav /app/run_direct_recorder.sh /app/run_direct_poster.sh /app/run_daily_summary.sh /app/run_cleanup.sh /app/healthcheck.py /app/scripts/fetch_pistar_password.sh \
    && mkdir -p /data/recordings /data/state /data/logs /config

VOLUME ["/data", "/config"]

CMD ["./run_direct_poster.sh"]
