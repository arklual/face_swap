# Face Swap Application

Приложение для замены лиц на фотографиях с использованием AI моделей.

## Структура проекта

- `backend/` - FastAPI бэкенд
- `faceapp-front/` - React фронтенд
- `comfyui/` - ComfyUI для обработки изображений
- `models/` - AI модели (не включены в репозиторий)
- `scripts/` - Вспомогательные скрипты

## Установка моделей

Модели не включены в репозиторий из-за их размера. Необходимо скачать следующие модели:

### ComfyUI модели (папка `comfyui/models_src/`)

#### checkpoints/
- `dreamshaper_8.safetensors` - основная модель Stable Diffusion

#### clip_vision/
- `CLIP-ViT-H-14-laion2B-s32B-b79K.safetensors`

#### controlnet/
- `control_v11p_sd15_lineart.pth`
- `control_v11p_sd15_openpose_fp16.safetensors`
- `control_v11p_sd15_openpose.pth`

#### ipadapter/
- `ip-adapter-faceid-plusv2_sd15.bin`

#### loras/
- `ip-adapter-faceid-plusv2_sd15_lora.safetensors`

#### upscale_models/
- `RealESRGAN_x2.pth`

### InsightFace модели (папка `models/insightface/models/`)
- `inswapper_128.onnx`

### ComfyUI annotators (ControlNet Aux)

Некоторые препроцессоры (например, **PiDiNet Soft-Edge Lines**) автоматически скачивают веса с Hugging Face (репо `lllyasviel/Annotators`) при первом запуске.

- Ожидаемый путь для PiDiNet: `comfyui/custom_nodes/comfyui_controlnet_aux/ckpts/lllyasviel/Annotators/table5_pidinet.pth`
- Если “зависает” на PiDiNet, можно заранее положить файл в `comfyui/models/table5_pidinet.pth` — при старте контейнера он будет скопирован в нужную папку.

Для **DWPose Estimator** веса тоже скачиваются с HF и могут быть большими:

- `dw-ll_ucoco_384_bs5.torchscript.pt` (~129MB) — поза (репо `hr16/DWPose-TorchScript-BatchSize5`)
- `yolox_l.onnx` (~207MB) — bbox detector (репо `yzd-v/DWPose`, используется по умолчанию в UI)

Если “зависает” на DWPose, проверь в ноде `bbox_detector` — значение `None` не требует скачивания детектора.

## Запуск

```bash
# Запуск через docker-compose
docker-compose up -d
```

## Разработка через Docker (watch/hot reload)

Бэкенд в dev-режиме стартует с `uvicorn --reload`, фронтенд — через Vite dev server.

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d --build
```

- Frontend dev: `http://localhost:8080/` (порт 8080)
- Backend API: `http://localhost:8000`

Если у вас Docker Compose с поддержкой `watch`, можно вместо `up` использовать:

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml watch
```

## Переменные окружения

Скопируйте `backend/env.example` в `backend/.env` и настройте необходимые параметры.

### Proxy для ComfyUI (опционально)

Если ComfyUI/кастомные ноды должны скачивать модели/веса через исходящий прокси, задайте переменные в **корневом** `.env` (он уже в `.gitignore`).

- Пример: скопируйте `env.example` в `.env` и укажите:
  - `COMFY_PROXY=socks5h://user:pass@host:port`
  - `COMFY_PROXY_ENABLED=0/1` (0 — прокси полностью отключён для comfyui, даже если `COMFY_PROXY` задан)
  - `COMFY_BUILD_PROXY=` (опционально; прокси только для сборки образа. `apt` не умеет SOCKS, поэтому для socks5 обычно оставляют пустым)
  - `COMFY_NO_PROXY=localhost,127.0.0.1,db,redis,web,celery_worker,comfyui`
  - Если ваш прокси “висит” на больших файлах с HF — добавьте в `COMFY_NO_PROXY`: `huggingface.co,cas-bridge.xethub.hf.co,cdn-lfs.huggingface.co` (тогда HuggingFace будет качаться напрямую, а остальное — через прокси)

Затем пересоберите/перезапустите:

```bash
docker compose up -d --build
```

По умолчанию кэш HuggingFace внутри `comfyui` сохраняется в volume (через `HF_HOME=/home/runner/ComfyUI/models/hf`), чтобы скачанные файлы не терялись при пересоздании контейнера.

## Разработка

### Backend
```bash
cd backend
pip install -r requirements.txt
python -m app.main
```

### Frontend
```bash
cd faceapp-front
npm install
npm run dev
```

