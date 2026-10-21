# Kestrel: one image, two flavours selected at build time.
#   GPU (default): CUDA 12.6 + cuDNN 9 runtime, torch 2.5.1+cu121, onnxruntime-gpu 1.20.2
#   CPU          : plain Ubuntu, torch 2.5.1+cpu, onnxruntime 1.18.1 (as in requirements.txt)
# Python 3.12 (Ubuntu 24.04) is required: positional-encodings==6.0.4 needs >=3.12.
# The compose files set the build args; see README "Docker".
ARG BASE_IMAGE=nvidia/cuda:12.6.3-cudnn-runtime-ubuntu24.04
FROM ${BASE_IMAGE}

ARG ACCEL=gpu
ENV DEBIAN_FRONTEND=noninteractive \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_BREAK_SYSTEM_PACKAGES=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONUTF8=1 \
    MPLBACKEND=Agg

# ffmpeg: browser-playable H.264 re-encode and clip export.
# python3-tk: badminton_analysis/system.py imports tkinter at module level.
# libgl1/libglib2.0-0t64: OpenCV. pango/cairo/harfbuzz: weasyprint PDF reports.
RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 python3-pip python3-tk ffmpeg curl ca-certificates \
        libgl1 libglib2.0-0t64 \
        libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz0b libcairo2 shared-mime-info \
        fonts-dejavu-core \
    && ln -sf /usr/bin/python3 /usr/local/bin/python \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Torch and onnxruntime are installed per flavour, so their lines (and the CPU index)
# are stripped out of requirements.txt below. That keeps the file untouched for the
# Windows flow.
RUN if [ "$ACCEL" = "gpu" ]; then \
      python -m pip install torch==2.5.1 torchvision==0.20.1 --index-url https://download.pytorch.org/whl/cu121; \
    else \
      python -m pip install torch==2.5.1+cpu torchvision==0.20.1+cpu --index-url https://download.pytorch.org/whl/cpu; \
    fi

COPY requirements.txt .
RUN grep -viE '^(--extra-index-url|torch|torchvision|onnxruntime)([=<>~ ]|$)' requirements.txt > /tmp/requirements.docker.txt \
    && python -m pip install -r /tmp/requirements.docker.txt \
    && if [ "$ACCEL" = "gpu" ]; then \
         python -m pip uninstall -y onnxruntime \
         && python -m pip install --force-reinstall --no-deps onnxruntime-gpu==1.20.2; \
       else \
         python -m pip install onnxruntime==1.18.1; \
       fi

COPY . .

# videos/, outputs/, data/ and weights/ are bind-mounted by compose.
EXPOSE 5050
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:5050/api/models', timeout=4).status == 200 else 1)"

CMD ["python", "app.py", "--host", "0.0.0.0", "--port", "5050"]
