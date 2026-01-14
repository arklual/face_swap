FROM python:3.11-slim

# Allow outbound proxy for build steps (apt/git/pip). Values come from docker-compose `build.args`.
ARG HTTP_PROXY
ARG HTTPS_PROXY
ARG ALL_PROXY
ARG NO_PROXY
ARG http_proxy
ARG https_proxy
ARG all_proxy
ARG no_proxy

RUN set -e; \
    # `apt` does not support SOCKS proxies (e.g. socks5://...), so if such a proxy is configured
    # via build args/environment, temporarily disable it for this step.
    proxy_url="${http_proxy:-${HTTP_PROXY:-}}"; \
    case "$proxy_url" in \
      socks*://*) \
        echo "Detected SOCKS proxy for build: $proxy_url"; \
        echo "Disabling proxy for apt-get (apt does not support SOCKS proxies)"; \
        unset http_proxy https_proxy all_proxy HTTP_PROXY HTTPS_PROXY ALL_PROXY no_proxy NO_PROXY; \
        ;; \
    esac; \
    apt-get update && apt-get install -y --no-install-recommends \
    git \
    wget \
    curl \
    unzip \
    build-essential \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

RUN useradd -m runner

USER runner
WORKDIR /home/runner
RUN git clone https://github.com/comfyanonymous/ComfyUI.git

WORKDIR /home/runner/ComfyUI
RUN pip install --no-cache-dir -r requirements.txt

RUN pip install --no-cache-dir \
    opencv-python \
    onnxruntime-gpu \
    controlnet-aux \
    einops \
    timm \
    numba \
    piexif \
    matplotlib \
    segment-anything \
    pysocks \
    insightface

RUN mkdir -p models/checkpoints models/clip models/vae models/controlnet models/ipadapter models/upscale

USER root
RUN chown -R runner:runner /home/runner/ComfyUI
USER runner

RUN mkdir -p /home/runner/ComfyUI/custom_nodes && cd /home/runner/ComfyUI/custom_nodes \
    && git clone --depth 1 https://github.com/cubiq/ComfyUI_IPAdapter_plus.git \
    && git clone --depth 1 https://github.com/ltdrdata/ComfyUI-Impact-Pack.git \
    && git clone --depth 1 https://github.com/Kosinkadink/ComfyUI-Advanced-ControlNet.git \
    && git clone --depth 1 https://github.com/Fannovel16/comfyui_controlnet_aux.git \
    && git clone --depth 1 https://github.com/WASasquatch/was-node-suite-comfyui.git \
    && git clone --depth 1 https://github.com/cubiq/ComfyUI_essentials.git \
    && git clone --depth 1 https://github.com/kijai/ComfyUI-KJNodes.git || true

RUN mkdir -p models/checkpoints models/clip models/vae models/controlnet models/ipadapter models/upscale models/insightface

USER root
RUN chown -R runner:runner /home/runner/ComfyUI
USER runner

USER root
COPY models_src/ /opt/comfyui_models_baked/
RUN chown -R runner:runner /opt/comfyui_models_baked || true
USER runner

EXPOSE 8188

COPY entrypoint.sh /home/runner/entrypoint.sh
USER root
RUN chmod +x /home/runner/entrypoint.sh && chown runner:runner /home/runner/entrypoint.sh
USER runner
ENTRYPOINT ["/home/runner/entrypoint.sh"]