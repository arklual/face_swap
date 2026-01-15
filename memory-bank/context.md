# Current focus
Updated: 2026-01-14 (assistant)

Personalization preview: одиночные страницы; фронт показывает 0, 2–22, 24 (1 и 23 скрыты).

# Last completed step
Updated: 2026-01-15 (assistant)

- Backend: prepay генерация жёстко берёт первую/последнюю видимые страницы (0 и 24).
- Backend: превью использует только manifest-ключи (без fallback на page_01).
- Backend: front preview фильтрует только 1 и 23.

# Next steps
Updated: 2026-01-15 (assistant)

- Проверить на стенде предпросмотр: страницы 0, 2–22, 24; prepay=0 и 24; навигация/скачивание.
- Для старых персонализаций перегенерировать prepay, чтобы появилась page_24.

# Open questions / risks
Updated: 2026-01-14 (assistant)

- Пайплайн переплёта (front_cover/back_cover, логотипы, PDF переплёта) ещё не реализован.
