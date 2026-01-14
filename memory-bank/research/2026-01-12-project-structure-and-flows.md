## Что исследовано

Репозиторий `face_swap` содержит:

- FastAPI backend (`backend/`)
- SPA фронтенд (`wonderwraps/`)
- ComfyUI контейнер (`comfyui/`)
- локальные модели/fallback (`models/insightface/`)

Документы требований/контекста:

- `ts.txt` — требования к книжному пайплайну (24 страницы + обложки, DPI/размеры, превью-развороты, PDF).
- `BACKEND_RENDER_INTEGRATION_PLAN.md` — план интеграции рендера/пайплайна (частично реализован кодом).

## Факты по backend

### Основные модули

- API entrypoint: `backend/app/main.py`
- Config: `backend/app/config.py` (+ `backend/env.example`)
- DB: `backend/app/db.py` (async SQLAlchemy)
- Models: `backend/app/models.py`
- Schemas: `backend/app/schemas.py`
- Auth: `backend/app/auth.py`
- Exceptions: `backend/app/exceptions.py`

### Celery

- `backend/app/workers.py`: `celery_app` с роутингом задач по очередям:
  - `gpu`: `analyze_photo_task`, `generate_image_task`, `build_stage_backgrounds_task`
  - `render`: `render_stage_pages_task`
- В docker-compose есть два воркера:
  - `celery_worker` (GPU queues)
  - `celery_render_worker` (render queue)

### S3 ключи (как используется сейчас)

Наблюдаемая схема ключей/префиксов:

- Входные фото:
  - `child_photos/{job_id}_{filename}`
  - `avatars/{job_id}_{filename}` (при замене аватара)
- Legacy результаты генерации (по иллюстрациям):
  - `results/{job_id}/{ill_id}.png`
- Manifest-driven пайплайн (layout):
  - backgrounds: `layout/{job_id}/pages/page_XX_bg.png`
  - финал: `layout/{job_id}/pages/page_XX.png`
- Шаблоны книг:
  - `templates/{slug}/manifest.json` (обязательный контракт для нового пайплайна)

### Manifest (контракт для генерации)

Формат задаётся `backend/app/book/manifest.py`:

- `pages[]`: `page_num`, `base_uri`, `needs_face_swap`, `text_layers[]`, `availability`, optional prompt controls
- `text_layers[]`: `text_template|text_key`, `template_engine`, `style`, optional `font_uri`
- `output`: `dpi`, `page_size_px`

### Рендер текста

`backend/app/rendering/html_text.py`:

- Playwright рендерит HTML в PNG
- Фон и шрифты встраиваются через `data:` URI
- Текст экранируется (`html.escape`)
- Внешние запросы блокируются (route interception)

### Inference

`backend/app/inference/comfy_runner.py`:

- ComfyUI REST API интеграция (upload → prompt → history → view)
- Workflow адаптируется из `workflow.json`
- Маска лица формируется как отдельная картинка (не alpha-channel), чтобы избежать падений `ImageToMask(alpha)`
- Fallback на локальный InsightFace

## Факты по frontend (`wonderwraps`)

- Entry: `src/main.tsx` → `AppProviders`
- Providers: react-query, session sync, router, toaster
- API: Orval-generated client (`src/shared/api/generated/*`) использует `customFetch`:
  - добавляет JWT из `localStorage` (`STORAGE_KEYS.SESSION_TOKEN`)
  - на 401 инвалидирует сессию и чистит кеши
- Основные флоу на UI:
  - каталог → персонализация → корзина → checkout → заказ

## Наблюдения/вопросы (не решения)

- `ts.txt` описывает, что “самая первая страница превью” — crop обложки, но текущая `prepay` реализация возвращает строго `page_01`+`page_02`.
- В backend `auth.py` используются исключения `jwt.JWTError`, которых нет в PyJWT (нужно поправить).

