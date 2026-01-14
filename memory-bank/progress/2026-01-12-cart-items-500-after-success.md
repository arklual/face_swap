## Контекст

Инцидент: операции корзины в UI (добавление/удаление) могли показывать ошибку, хотя фактически изменение в БД происходило и становилось видно после перезагрузки/рефетча.

Наблюдение со стороны API: `POST /cart/items` отдавал 500, но запись о позиции корзины успевала создаваться (после refresh страницы товар был в корзине).

## Причина

В `SQLAlchemy AsyncSession` по умолчанию включён `expire_on_commit`. После `await db.commit()` атрибуты ORM-объектов помечаются “expired”, и последующий доступ к ним может попытаться сделать lazy-load запрос к БД.

В async-контексте такой lazy-load нередко падает с ошибками уровня `MissingGreenlet`/неожиданным IO при сериализации ответа.

В `POST /cart/items` сразу после commit возвращался `await _build_cart_response(cart, db)`, который читает поля `cart` (например `updated_at`), что и приводило к 500 **после успешного commit**.

## Решение

- `backend/app/routes/cart.py`
  - В `POST /cart/items` добавлено `await db.refresh(cart)` после `commit()` в обеих ветках (existing item / new item).
  - Дополнительно “touch” корзины (`cart.currency = cart.currency`) в ветке увеличения quantity, чтобы обновлялся `updated_at`.
  - `DELETE /cart/items/{itemId}` сделан идемпотентным: если item не найден — возвращаем 204 (не 404), и добавлен try/except с rollback и кодом `REMOVE_FROM_CART_FAILED` на неожиданные ошибки.

## Ожидаемое поведение

- `POST /cart/items` всегда возвращает 200 с валидным JSON `Cart` (без 500 “после commit”).
- `DELETE /cart/items/{itemId}` возвращает 204 даже если item уже удалён/перемещён/слит, чтобы UI не показывал ложные ошибки.

