FROM mambaorg/micromamba:2.3.3@sha256:800e7ade3ffe29c9a9ac2026163131495f8197c3852e572c5835beb4e8a33cd6
ARG TARGETARCH
ARG DEVICE=cpu
USER root
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg default-jre-headless \
    && rm -rf /var/lib/apt/lists/*
COPY locks /tmp/seungun-locks
COPY container/kalpy-cpu /tmp/kalpy-cpu
ENV FINISH=/workspace/intelligentai/validation/finish_20260916
# Preserve the deployed AMD64 Python base. GPU MFA links libcuda.so.1 directly;
# Kalpy's official runtime contract permits either Kaldi variant at this version.
# AMD64 CPU selects CPU Kaldi; ARM must also compile the missing Kalpy binary.
RUN if [ "$TARGETARCH" = "amd64" ]; then \
      micromamba create -y -p "$FINISH/envs/mac_prod311" -f /tmp/seungun-locks/app-conda-linux-64.explicit; \
    else \
      micromamba create -y -p "$FINISH/envs/mac_prod311" -c conda-forge python=3.11.15 pip; \
    fi && \
    if [ "$DEVICE" = "cuda" ] && [ "$TARGETARCH" = "amd64" ]; then \
      micromamba create -y -p "$FINISH/envs/mfa341" -f /tmp/seungun-locks/mfa-linux-64.explicit; \
    elif [ "$TARGETARCH" = "amd64" ]; then \
      micromamba create -y -p "$FINISH/envs/mfa341" -c conda-forge \
        python=3.14.7 montreal-forced-aligner=3.4.1 kalpy=0.10.5 'kaldi=5.5.1172=cpu_*'; \
    else \
      micromamba create -y -n builder -c conda-forge python=3.12 conda-build conda && \
      micromamba run -n builder conda build --no-test --override-channels -c conda-forge \
        --output-folder /tmp/local-conda /tmp/kalpy-cpu && \
      micromamba create -y -p "$FINISH/envs/mfa341" -c file:///tmp/local-conda -c conda-forge \
        python=3.14.7 montreal-forced-aligner=3.4.1 kalpy=0.10.5 'kaldi=5.5.1172=cpu_*' && \
      micromamba env remove -y -n builder; \
    fi
COPY container/mfa-cpu-specs.txt /tmp/mfa-cpu-specs.txt
RUN if [ "$DEVICE" = "cpu" ]; then \
      micromamba install -y -p "$FINISH/envs/mfa341" -c conda-forge -f /tmp/mfa-cpu-specs.txt; \
    fi
# Native ARM wheels are not available for every acoustic dependency. Retain a
# compiler/JNI toolchain for source distributions rather than using x86 wheels.
RUN if [ "$TARGETARCH" = "arm64" ]; then \
      apt-get update && apt-get install -y --no-install-recommends \
        default-jdk-headless build-essential cmake ninja-build pkg-config libffi-dev && \
      rm -rf /var/lib/apt/lists/*; \
    fi
RUN if [ "$DEVICE" = "cpu" ]; then INDEX=cpu; \
    elif [ "$DEVICE" = "cuda" ] && [ "$TARGETARCH" = "amd64" ]; then INDEX=cu126; \
    else exit 2; fi && \
    "$FINISH/envs/mac_prod311/bin/python" -m pip install --no-cache-dir torch==2.13.0 --index-url "https://download.pytorch.org/whl/$INDEX" && \
    "$FINISH/envs/mac_prod311/bin/python" -m pip install --no-cache-dir -r /tmp/seungun-locks/app-requirements.txt && \
    mkdir -p /opt/seungun-build && \
    "$FINISH/envs/mac_prod311/bin/python" -m pip freeze > /opt/seungun-build/installed-app.txt && \
    micromamba list -p "$FINISH/envs/mfa341" --explicit > /opt/seungun-build/installed-mfa.explicit && \
    dpkg-query -W > /opt/seungun-build/system-packages.txt && \
    micromamba clean --all --yes
ENV SEUNGUN_IMAGE_DEVICE=${DEVICE}
ENV PATH=/workspace/intelligentai/validation/finish_20260916/envs/mac_prod311/bin:/usr/bin:/bin
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /opt/seungun
COPY . /opt/seungun
RUN mkdir -p /assets /runtime
ENTRYPOINT ["/workspace/intelligentai/validation/finish_20260916/envs/mac_prod311/bin/python", "-B", "/opt/seungun/local/entry.py"]
