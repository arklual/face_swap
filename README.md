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

## Запуск

```bash
# Запуск через docker-compose
docker-compose up -d
```

## Переменные окружения

Скопируйте `backend/env.example` в `backend/.env` и настройте необходимые параметры.

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

