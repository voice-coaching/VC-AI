FROM nvidia/cuda:13.0.2-base-ubuntu24.04@sha256:2ab6381d970b211fb93853796dc6707eb8a72575a375c422b17cf4d8b2641701

ARG VCS_REF=unknown

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HF_HUB_DISABLE_TELEMETRY=1 \
    HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1 \
    TOKENIZERS_PARALLELISM=false \
    PYTHONPATH=/app/src:/app/seungun/src \
    PATH=/app/.venv/bin:/usr/bin:/bin

RUN apt-get update \
    && apt-get install --yes --no-install-recommends \
        ffmpeg libseccomp2 libsndfile1 openjdk-21-jre-headless passwd \
        python3.12 python3.12-venv python3-pip \
    && rm -rf /var/lib/apt/lists/* \
    && /usr/sbin/groupadd --gid 10001 voice-coach \
    && /usr/sbin/useradd --uid 10001 --gid 10001 --no-create-home --home-dir /nonexistent voice-coach

WORKDIR /app

COPY pyproject.toml /app/pyproject.toml
COPY requirements/backend-analysis-worker.txt /app/requirements/backend-analysis-worker.txt
COPY requirements/paper-g2pk-linux-aarch64.lock.txt /app/requirements/paper-g2pk-linux-aarch64.lock.txt
COPY src /app/src
COPY seungun/src /app/seungun/src
COPY configs/pronunciation_feedback /app/configs/pronunciation_feedback
COPY scripts/g2pk_expected_phones_worker.py /app/scripts/g2pk_expected_phones_worker.py
COPY scripts/run_backend_analysis_worker.py /app/scripts/run_backend_analysis_worker.py

RUN python3.12 -m venv /app/.venv \
    && /app/.venv/bin/python -m pip install --no-cache-dir \
        -e '.[scorer,visual]' -r requirements/backend-analysis-worker.txt \
    && python3.12 -m venv /app/.venv-paper-g2pk \
    && /app/.venv-paper-g2pk/bin/python -m pip install --no-cache-dir \
        -r requirements/paper-g2pk-linux-aarch64.lock.txt \
    && /app/.venv/bin/python -c \
        'import boto3, mediapipe, redis, soundfile, torch, transformers; import korean_phone_ctc' \
    && /app/.venv-paper-g2pk/bin/python -c \
        'from importlib.metadata import version; import jpype, konlpy; assert version("g2pK") == "0.9.4"' \
    && chown -R root:root /app \
    && chmod -R a-w /app

LABEL org.opencontainers.image.revision="${VCS_REF}" \
      org.opencontainers.image.source="https://github.com/voice-coaching/intelligentAI"

ENV VOICE_COACH_BUILD_REVISION=${VCS_REF}

USER 10001:10001

ENTRYPOINT ["/app/.venv/bin/python", "/app/scripts/run_backend_analysis_worker.py"]
CMD ["--block-ms", "1000"]
