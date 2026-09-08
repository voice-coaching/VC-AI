FROM nvidia/cuda:13.0.2-base-ubuntu24.04@sha256:2ab6381d970b211fb93853796dc6707eb8a72575a375c422b17cf4d8b2641701

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HF_HUB_DISABLE_TELEMETRY=1 \
    HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1 \
    TOKENIZERS_PARALLELISM=false \
    HOME=/tmp \
    HF_MODULES_CACHE=/tmp/huggingface-modules

RUN apt-get update \
    && apt-get install --yes --no-install-recommends python3.12 python3-pip \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY deploy/hyperclova/requirements.txt /tmp/hyperclova-requirements.txt
RUN python3.12 -m pip install --break-system-packages \
      --no-cache-dir -r /tmp/hyperclova-requirements.txt \
    && rm /tmp/hyperclova-requirements.txt

COPY src /app/src
COPY deploy/hyperclova/entrypoint.py /app/deploy/hyperclova/entrypoint.py
COPY scripts/serve_hyperclovax_grounded_advice.py /app/scripts/serve_hyperclovax_grounded_advice.py
COPY configs/pronunciation_feedback/hyperclovax_seed_think_32b_v1.json /app/configs/pronunciation_feedback/hyperclovax_seed_think_32b_v1.json

EXPOSE 8000

ENTRYPOINT ["python3.12", "deploy/hyperclova/entrypoint.py"]
CMD ["--host", "0.0.0.0", "--port", "8000", "--gpu-memory-gib", "110"]
