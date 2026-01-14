## Обзор

Проект `face_swap` — это связка e-commerce SPA (WonderWraps/MagicLoomio) + AI-пайплайн персонализации (face transfer + рендер текста) вокруг единого API.

Ключевые подсистемы:

- **Backend API**: FastAPI (`backend/`) — каталог, персонализации, корзина, заказы, аккаунт.
- **Workers**: Celery воркеры — GPU-очередь (инференс) + CPU/render-очередь (рендер текста).
- **ComfyUI**: отдельный контейнер (`comfyui/`) для инференса (face transfer workflow) с GPU.
- **Хранилища**:
  - Postgres — основная БД (каталог/корзина/заказы/персонализации).
  - Redis — broker/result-backend для Celery.
  - S3 (совместимое) — шаблоны, входные фото, результаты генерации, preview-страницы.
- **Frontend SPA**: `wonderwraps/` (Vite + React + TS) — общается с backend через OpenAPI-генерируемый типизированный клиент.

## Репозиторий: важные директории

- `backend/` — FastAPI, Celery задачи, SQLAlchemy модели, рендер текста (Playwright), инференс (ComfyUI/InsightFace), OpenAPI.
- `wonderwraps/` — SPA фронтенд (Vite/React), Orval-generated API клиент.
- `comfyui/` — Docker образ ComfyUI + custom nodes + baked models.
- `models/insightface/` — локальные модели InsightFace (используются как fallback).
- `openapi.json` (в корне) — экспорт OpenAPI из backend.

## Backend (FastAPI)

### Entrypoint и маршрутизация

- **Entrypoint**: `backend/app/main.py`
  - Поднимает `FastAPI(...)` с тегами и кастомным `openapi()` (добавляет `bearerAuth`).
  - Вешает обработчики исключений: `FaceAppBaseException`, `HTTPException`, generic `Exception`.
  - На startup делает `Base.metadata.create_all` (без миграций).
  - Подключает роуты:
    - `backend/app/routes/auth.py` (`/auth/*`)
    - `backend/app/routes/catalog.py` (`/books*`)
    - `backend/app/routes/personalizations.py` (`/upload_and_analyze/`, `/generate/`, `/preview/*`, etc.)
    - `backend/app/routes/cart.py` (`/cart*`, `/checkout/*`)
    - `backend/app/routes/orders.py` (`/checkout/orders`, `/orders*`)
    - `backend/app/routes/account.py` (`/account/profile`)

### Конфигурация и окружение

- `backend/app/config.py`: `Settings(BaseSettings)` читает `.env` (см. `backend/env.example`).
- Важное:
  - **DB**: `DATABASE_URL`
  - **S3**: `AWS_ENDPOINT_URL`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_REGION_NAME`, `S3_BUCKET_NAME`
  - **Celery**: `CELERY_BROKER_URL`, `CELERY_RESULT_BACKEND`
  - **ComfyUI**: `COMFY_BASE_URL`
  - **JWT**: `JWT_SECRET_KEY`, `JWT_ALGORITHM`, `JWT_ACCESS_TOKEN_EXPIRE_MINUTES`

### База данных (SQLAlchemy модели)

Файл: `backend/app/models.py`

- **Персонализации**:
  - `jobs` (`Job`): `job_id`, `user_id`, `slug`, `status`, `child_photo_uri`, `child_name`, `child_age`, `avatar_url`, `cart_item_id`, etc.
  - `job_artifacts` (`JobArtifact`): `job_id`, `stage` (`prepay|postpay`), `kind` (`page_png`, `page_bg_png`, ...), `page_num`, `s3_uri`, `meta`.
- **Пользователи**:
  - `users` (`User`)
  - `user_delivery_addresses` (`UserDeliveryAddress`)
  - `password_reset_tokens` (`PasswordResetToken`)
- **Каталог**:
  - `books` (`Book`)
  - `book_previews` (`BookPreview`) (страницы для витринного preview)
- **Корзина/заказы**:
  - `carts` (`Cart`)
  - `cart_items` (`CartItem`) (ссылка на `jobs.job_id` как `personalization_id`)
  - `orders` (`Order`) + `order_items` (`OrderItem`) (`OrderStatus` enum)

### Авторизация

- `backend/app/routes/auth.py`:
  - `POST /auth/signup`, `POST /auth/login` → возвращают JWT `token` + `UserProfile`.
  - `POST /auth/logout` (на клиенте токен просто удаляется).
  - `POST /auth/forgot-password` → создаёт `PasswordResetToken` (email-отправка TODO).
  - `POST /auth/reset-password`.
- `backend/app/auth.py`:
  - `get_current_user` использует `HTTPBearer` и проверяет JWT.
  - `get_current_user_optional` — “мягкая” авторизация по `Authorization: Bearer ...`.

### Каталог

- `backend/app/routes/catalog.py`: `GET /books`, `GET /books/filters`, `GET /books/highlights`, `GET /books/{slug}`, `GET /books/{slug}/related`, `GET /books/{slug}/previews`.
- Картинки в моделях хранятся как URL/ключи; сервер генерирует presigned GET URL через boto3.

### Персонализация (Jobs) и пайплайн генерации

Файл: `backend/app/routes/personalizations.py`

Основной контракт:

- `POST /upload_and_analyze/` (multipart):
  - Загружает фото ребёнка в S3 (`child_photos/...`), создаёт `Job(status=pending_analysis)`.
  - Ставит задачу `analyze_photo_task` в очередь `gpu`.
- `GET /status/{job_id}`:
  - Возвращает `Personalization` + (при наличии) `preview`.
- `POST /generate/` (form-urlencoded):
  - Сохраняет `child_name`/`child_age`, переводит в **prepay** стадию и ставит `build_stage_backgrounds_task(job_id, "prepay")` в очередь `gpu`.
- `GET /preview/{job_id}?stage=prepay|postpay`:
  - Manifest-driven preview по S3 ключам `layout/{job_id}/pages/page_XX.png`.
- `GET /result/{job_id}`:
  - Legacy preview: подмешивает `results/{job_id}/*.png` поверх `BookPreview` (fallback, если manifest-пайплайн недоступен).
- `POST /avatar/{job_id}`:
  - Обновляет фото/аватар, рестартит анализ.

#### Стадии (prepay/postpay)

- Логика стадий описана в `backend/app/book/stages.py`:
  - `prepay`: **строго page_01 и page_02**.
  - `postpay`: страницы, у которых `availability.postpay == true` в manifest.

#### Celery задачи (производственный пайплайн)

Файл: `backend/app/tasks.py`

- **GPU стадия**: `build_stage_backgrounds_task(job_id, stage)`
  - Загружает manifest (`templates/{slug}/manifest.json` из S3).
  - Для страниц стадии:
    - если `needs_face_swap`: запускает `_run_face_transfer(...)` (lazy import ComfyUI/InsightFace) и получает фон.
    - иначе — берёт `base_uri` как есть.
  - Пишет background в S3: `layout/{job_id}/pages/page_XX_bg.png`.
  - Создаёт `JobArtifact(kind="page_bg_png")`.
  - Ставит **CPU/render** задачу `render_stage_pages_task(job_id, stage)` в очередь `render`.

- **CPU стадия**: `render_stage_pages_task(job_id, stage)`
  - Загружает background `layout/{job_id}/pages/page_XX_bg.png`.
  - Накладывает текстовые слои (если `text_layers` в manifest) через `backend/app/rendering/html_text.py`.
  - Пишет финал в S3: `layout/{job_id}/pages/page_XX.png`.
  - Создаёт `JobArtifact(kind="page_png")`.
  - Обновляет статус job:
    - `prepay` → `prepay_ready`
    - `postpay` → `completed`

#### Manifest книги

- `backend/app/book/manifest_store.py` ожидает manifest в S3:
  - `templates/{slug}/manifest.json`
- Структура manifest (Pydantic): `backend/app/book/manifest.py`
  - `pages[]`: `page_num`, `base_uri`, `needs_face_swap`, `text_layers[]`, `availability`, (optional) `prompt/negative_prompt`
  - `output`: `dpi`, `page_size_px`

### Корзина и оформление заказа

- `backend/app/routes/cart.py`
  - `GET /cart` — текущая корзина (требует JWT).
  - `POST /cart/items` — добавление персонализации; при первом добавлении выставляет:
    - `job.cart_item_id = cart_item.id`
    - `job.status = "confirmed"`
  - `PATCH /cart/items/{itemId}`, `DELETE /cart/items/{itemId}`.
  - `GET /checkout/shipping-methods` — сейчас статический список (demo).
  - `POST /checkout/quote` — расчёт тоталов (demo tax=10%).
- `backend/app/services/cart.py`: `get_or_create_active_cart(...)` выбирает “активную” корзину и **мерджит дубликаты**.

### Заказы и postpay-триггер генерации

- `backend/app/routes/orders.py`
  - `POST /checkout/orders`:
    - создаёт order из cart items, удаляет позиции из корзины.
    - для `payment.provider == "test"` переводит order в `PROCESSING` и **триггерит postpay**:
      - `build_stage_backgrounds_task(personalization_id, "postpay")` для каждой позиции.
  - `POST /orders/{orderId}/mark_paid`:
    - “вебхук”/ручной эндпойнт: помечает заказ оплаченным (`PROCESSING`) и триггерит postpay.
  - `GET /orders`, `GET /orders/{orderId}`.

### Rendering: текст поверх картинки

Файл: `backend/app/rendering/html_text.py`

- Реализация рендера текста поверх фона через Playwright:
  - фон и (опционально) шрифт встраиваются как `data:` URI,
  - текст проходит `html.escape` (защита от инъекций),
  - внешние запросы в Chromium заблокированы через `page.route("**/*", ...)`.

### Inference: ComfyUI + fallback

Файл: `backend/app/inference/comfy_runner.py`

- Основной путь: ComfyUI REST API:
  - upload `/upload/image` → queue `/prompt` → poll `/history/{prompt_id}` → download `/view`.
  - Workflow берётся из `backend/app/inference/workflow.json` и адаптируется под входные filename/prompt.
  - Маска лица генерируется автоматически (ellipse + blur), чтобы избежать падений `ImageToMask(channel=alpha)`.
- Fallback: локальный InsightFace (`run_face_transfer_local`) при проблемах с ComfyUI.

## Frontend (wonderwraps SPA)

### Стек и entrypoints

- **Stack**: Vite + React 19 + TypeScript, React Router, TanStack Query, Jotai, SCSS modules, Orval (OpenAPI).
- `wonderwraps/src/main.tsx` → `AppProviders`.
- `wonderwraps/src/app/providers/AppProviders.tsx`:
  - `QueryClientProvider` (react-query)
  - `SessionSync` (очистка токена/кеша при 401)
  - `TypographyGuard`
  - `RouterProvider`
  - `sonner` toaster

### Роутинг

- `wonderwraps/src/app/routes/routes.tsx`:
  - lazy-loaded страницы: home, books, book detail, personalization, cart, checkout, account, orders, etc.

### API слой

- `wonderwraps/src/shared/api/fetcher.ts`: `customFetch`
  - автоматически добавляет `Authorization: Bearer <token>` из storage,
  - при `401` с токеном → удаляет токен и диспатчит `sessionInvalidated`,
  - бросает `ApiError` с данными ответа.
- `wonderwraps/src/shared/api/generated/*`: Orval-generated клиент (использует `customFetch`).
- `wonderwraps/src/shared/api/wonderwraps.ts`: удобные typed wrappers + проверка expected HTTP statuses.

### Сессия/стейт

- `wonderwraps/src/shared/state/sessionAtom.ts`: `sessionTokenAtom` хранится в `localStorage` (`STORAGE_KEYS.SESSION_TOKEN`).
- `wonderwraps/src/app/providers/SessionSync.tsx`:
  - слушает событие “сессия инвалидирована” и чистит токен + react-query кеш (`profile`, `cart`, `personalization-jobs`).

### Checkout flow (как реализовано сейчас)

- `wonderwraps/src/app/routes/checkout/Checkout.tsx`:
  - `fetchCart` → `fetchShippingMethods` → `fetchCheckoutQuote` → `createOrder`.
  - Сохраняет черновик формы в `localStorage` (`checkout.form-data`) и последнюю покупку (`checkout.last-order`).
  - Для демо использует `payment.provider="test"`.

## Инфраструктура / запуск (docker-compose)

- `docker-compose.yml`:
  - `db` (Postgres 15), `redis` (Redis 7)
  - `web` (FastAPI на `:8000`)
  - `celery_worker` (очереди `gpu,celery`, GPU)
  - `celery_render_worker` (очередь `render,celery`, CPU)
  - `comfyui` (GPU, `:8188`)
  - `frontend` (Nginx, `:80`)
- `docker-compose.dev.yml`:
  - `web` с `uvicorn --reload`
  - `frontend_dev` (Vite dev server на `:80` → `:5173` внутри)

Docker-образы:

- `backend/Dockerfile` — CUDA base image + Python deps + Playwright Chromium (нужен для render стадии).
- `comfyui/Dockerfile` — ComfyUI + custom nodes + baked модели, с поддержкой proxy через env.
- `wonderwraps/Dockerfile` — build → nginx runner; `wonderwraps/Dockerfile.dev` — dev.

