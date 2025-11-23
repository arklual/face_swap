#!/bin/sh
set -e

BAKED="/opt/comfyui_models_baked"
DEST="/home/runner/ComfyUI/models"

if [ -d "$BAKED" ] && [ -d "$DEST" ]; then
  find "$BAKED" -type f | while IFS= read -r src; do
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

exec python main.py --listen --port 8188