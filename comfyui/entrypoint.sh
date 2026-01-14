#!/bin/sh
set -e

# Runtime switch: allow keeping proxy values in `.env` but disable them without editing URLs.
# If COMFY_PROXY_ENABLED is 0/false/off, ComfyUI will run without any proxy env vars.
case "${COMFY_PROXY_ENABLED:-1}" in
  0|false|FALSE|no|NO|off|OFF)
    unset HTTP_PROXY HTTPS_PROXY ALL_PROXY NO_PROXY
    unset http_proxy https_proxy all_proxy no_proxy
    ;;
esac

BAKED="/opt/comfyui_models_baked"
DEST="/home/runner/ComfyUI/models"

if [ -d "$BAKED" ] && [ -d "$DEST" ]; then
  find "$BAKED" -type f | while IFS= read -r src; do
    # Skip zero-byte placeholders (they block automatic downloads later and can cause node hangs).
    if [ ! -s "$src" ]; then
      continue
    fi
    rel=${src#"$BAKED"/}
    dst="$DEST/$rel"
    ddir=`dirname "$dst"`
    if [ ! -d "$ddir" ]; then
      mkdir -p "$ddir" 2>/dev/null || true
    fi
    if [ ! -f "$dst" ] || [ "$src" -nt "$dst" ]; then
      cp "$src" "$dst" 2>/dev/null || true
    fi
  done
fi

# Remove known zero-byte placeholders from the shared models folder (they can prevent downloads).
YOLONAS_M="/home/runner/ComfyUI/models/yolo_nas_m_fp16.onnx"
if [ -f "$YOLONAS_M" ] && [ ! -s "$YOLONAS_M" ]; then
  rm -f "$YOLONAS_M" 2>/dev/null || true
fi

# If PiDiNet annotator weights are provided in the shared models folder, place them where
# comfyui_controlnet_aux expects them to avoid "hanging" on first-run downloads.
PIDINET_SRC="/home/runner/ComfyUI/models/table5_pidinet.pth"
PIDINET_DST_DIR="/home/runner/ComfyUI/custom_nodes/comfyui_controlnet_aux/ckpts/lllyasviel/Annotators"
PIDINET_DST="$PIDINET_DST_DIR/table5_pidinet.pth"
if [ -s "$PIDINET_SRC" ] && [ ! -s "$PIDINET_DST" ]; then
  mkdir -p "$PIDINET_DST_DIR" 2>/dev/null || true
  cp "$PIDINET_SRC" "$PIDINET_DST" 2>/dev/null || true
fi

# DWPose: if weights are present in the shared models folder, place them where
# comfyui_controlnet_aux expects them to avoid long first-run downloads.
DWPOSE_TS_SRC="/home/runner/ComfyUI/models/dw-ll_ucoco_384_bs5.torchscript.pt"
DWPOSE_TS_DST_DIR="/home/runner/ComfyUI/custom_nodes/comfyui_controlnet_aux/ckpts/hr16/DWPose-TorchScript-BatchSize5"
DWPOSE_TS_DST="$DWPOSE_TS_DST_DIR/dw-ll_ucoco_384_bs5.torchscript.pt"
if [ -s "$DWPOSE_TS_SRC" ] && [ ! -s "$DWPOSE_TS_DST" ]; then
  mkdir -p "$DWPOSE_TS_DST_DIR" 2>/dev/null || true
  cp "$DWPOSE_TS_SRC" "$DWPOSE_TS_DST" 2>/dev/null || true
fi

# YOLO-NAS (optional bbox detector for DWPose)
YOLONAS_DIR="/home/runner/ComfyUI/custom_nodes/comfyui_controlnet_aux/ckpts/hr16/yolo-nas-fp16"
for f in yolo_nas_s_fp16.onnx yolo_nas_m_fp16.onnx yolo_nas_l_fp16.onnx; do
  SRC="/home/runner/ComfyUI/models/$f"
  DST="$YOLONAS_DIR/$f"
  # If a zero-byte placeholder exists, remove it so the node can download a real file.
  if [ -f "$DST" ] && [ ! -s "$DST" ]; then
    rm -f "$DST" 2>/dev/null || true
  fi
  if [ -s "$SRC" ] && [ ! -s "$DST" ]; then
    mkdir -p "$YOLONAS_DIR" 2>/dev/null || true
    cp "$SRC" "$DST" 2>/dev/null || true
  fi
done

exec python main.py --listen --port 8188 --gpu-only