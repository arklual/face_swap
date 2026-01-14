## Контекст

Инцидент: `GET /preview/{id}?stage=prepay` иногда возвращал 500 при:

- неверном/несуществующем идентификаторе,
- передаче `cart_item_id` вместо `job_id`,
- некорректном состоянии job для запрошенной стадии.

Цель: вместо 500 возвращать корректные 404/400 и поддержать `cart_item_id` как “defensive fallback”.

## Сделано

- Приведены к корректным HTTP статусам доменные ошибки:
  - `JOB_NOT_FOUND` → 404 (`JobNotFoundError`)
  - `INVALID_JOB_STATE` → 400 (`InvalidJobStateError`)
- В `GET /preview/{job_id}` добавлен fallback-резолвинг:
  - сначала поиск по `jobs.job_id`,
  - затем поиск по `jobs.cart_item_id` (если UI передал id позиции корзины).
- (Эксплуатация) При необходимости выполнена очистка персонализаций и зависимых сущностей:
  - команда: `backend/scripts/purge_jobs.py --yes` (destructive)
  - зафиксированные значения до/после (по логам):
    - before: jobs=19, job_artifacts=32, cart_items=1, order_items=5
    - after: jobs=0, job_artifacts=0, cart_items=0, order_items=0

## Итоговое ожидаемое поведение

- Если id не найден (ни как `job_id`, ни как `cart_item_id`) → 404.
- Если job найден, но `stage=prepay|postpay` недоступен для текущего `job.status` → 400.
- Для валидных идентификаторов и состояний:
  - `stage=prepay` возвращает превью для первых страниц,
  - `stage=postpay` требует полной готовности (`completed`) и возвращает полный набор.

## Примечание (2026-01-12)

- `jobs.status="confirmed"` выставляется при добавлении персонализации в корзину (`POST /cart/items`) и может перезаписать `prepay_ready`.
- Поэтому `GET /preview/{job_id}?stage=prepay` должен трактовать `confirmed` как допустимое состояние для prepay-превью (иначе возникает `INVALID_JOB_STATE` при корректном job).

