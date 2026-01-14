## Open

- **Duplicate carts per user**
  - **Impact**: frontend может отправить `cartId` пустой корзины, пока товары лежат в другой корзине того же пользователя → `CART_EMPTY` на checkout.
  - **Status**: workaround в backend через active-cart selection + merge (`backend/app/services/cart.py`). DB-level уникальность по `carts.user_id` не enforced.

- **Personalization preview “stuck” on generating screen**
  - **Описание**: `BookPersonalization` включал загрузку превью (`previewQuery`) только при `step=preview`, но переход на `step=preview` происходил только при наличии `previewQuery.data` → циклическая блокировка. При `status=prepay_ready` экран мог навсегда оставаться на “Предпросмотр готов”.
  - **Impact**: пользователь не видит страницы предпросмотра и не может добавить в корзину, хотя бэк уже вернул `preview`.
  - **Status**: fixed
  - **Fix**:
    - `wonderwraps/src/app/routes/personalization/BookPersonalization.tsx`: авто-переход `generating → preview` при `preview/previewReadyAt/prepay_ready/completed`.
    - `wonderwraps/src/app/routes/personalization/components/GenerationStatus.tsx`: кнопка “Добавить в корзину” показывается также для `prepay_ready`.

- **Frontend `npm run typecheck` падает**
  - **Описание**: TypeScript typecheck сейчас падает на несоответствии пропсов (`EmptyStateProps`, `ErrorStateProps`) и на несуществующих переменных в `GenerationStatus`.
  - **Impact**: CI/сборка фронта могут падать даже если изменения не затрагивают эти файлы.
  - **Status**: open
  - **Ошибки**:
    - `src/app/routes/my-books/MyBooks.tsx`: `action` prop не существует в `EmptyStateProps`
    - `src/app/routes/order/OrderDetail.tsx`: `children` prop не существует в `ErrorStateProps`
    - `src/app/routes/personalization/components/GenerationStatus.tsx`: `onAddToCart` / `isAdding` не определены

- **Prepay/postpay определение “первых страниц”**
  - **Описание**: в `backend/app/book/stages.py` стадия `prepay` жёстко возвращает `page_01` + `page_02`.
  - **Impact**: в `ts.txt` “самая первая страница превью” — это crop обложки; требуется сверить продуктовый контракт и текущую реализацию stages/manifest.
  - **Status**: open (нужно согласование)

- **Создание схемы БД через `create_all`**
  - **Описание**: `backend/app/main.py` на startup делает `Base.metadata.create_all`.
  - **Impact**: нет миграций (Alembic), сложно безопасно эволюционировать схему в проде.
  - **Status**: open

- **Дублирование логики presigned URL**
  - **Описание**: `_presigned_get(...)` реализован отдельно в `backend/app/routes/catalog.py` и `backend/app/routes/personalizations.py`.
  - **Impact**: баги/фиксы легко “разъезжаются”, сложнее поддерживать разные форматы URL/endpoint.
  - **Status**: open (refactor candidate)

- **README в корне репозитория не соответствует текущей структуре**
  - **Описание**: `README.md` упоминает `faceapp-front/`, но в репо фактический фронт — `wonderwraps/`.
  - **Impact**: вводит в заблуждение при онбординге/запуске.
  - **Status**: open

- **OpenAPI/нейминг несогласован**
  - **Описание**: `wonderwraps/openapi.yaml` содержит `title: Face Transfer + MagicLoomio API`, тогда как фронтенд/репо используют `wonderwraps`.
  - **Impact**: путаница в брендинге/доках/генерации клиента.
  - **Status**: open

- **Seed data содержит жёстко заданные публичные S3 URL**
  - **Описание**: `backend/app/seed_data.py` использует `https://s3.twcstorage.ru/...` для картинок.
  - **Impact**: может не совпасть с настроенным `AWS_ENDPOINT_URL`/`S3_BUCKET_NAME`, seed становится “окружение-зависимым”.
  - **Status**: fixed
  - **Fix**:
    - `backend/app/seed_data.py` переведён на публичные CDN-URL (`storage.wonderwraps.com`) для hero/gallery и `book_previews`.
    - `_presigned_get(...)` в `backend/app/routes/catalog.py` и `backend/app/routes/personalizations.py` больше не пытается presign-ить чужие buckets (возвращает исходный URL).

## Fixed

- **JWT обработка ошибок в backend**
  - **Описание**: `backend/app/auth.py` ловил `jwt.JWTError`, которого нет в PyJWT.
  - **Impact**: невалидный/просроченный токен мог приводить к 500 вместо корректного 401.
  - **Status**: fixed
  - **Fix**: `decode_access_token` теперь ловит `jwt.exceptions.PyJWTError`.

- **JWT настройки частично игнорируются**
  - **Описание**: в `backend/app/auth.py` `ALGORITHM` и `ACCESS_TOKEN_EXPIRE_MINUTES` были захардкожены.
  - **Impact**: расхождение между `.env` и реальным поведением авторизации.
  - **Status**: fixed
  - **Fix**: берём `settings.JWT_ALGORITHM` / `settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES` (с дефолтами).

- **`GET /preview/{id}?stage=prepay` возвращал 500 на некорректные id**
  - **Status**: fixed (см. `memory-bank/progress/2026-01-12-preview-stage-prepay-500.md`)

- **Операции корзины “срабатывают, но API отвечает 500/ошибкой”**
  - **Описание**: `POST /cart/items` мог отдавать 500 после успешного `commit()` из-за expired-атрибутов AsyncSession и падения при сборке ответа; удаление могло ошибаться при already-deleted/merged itemId.
  - **Status**: fixed (см. `memory-bank/progress/2026-01-12-cart-items-500-after-success.md`)

