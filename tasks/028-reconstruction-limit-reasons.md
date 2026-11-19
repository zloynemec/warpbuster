# Task 028 — Конкретные причины ограничений reconstruction

## Цель

Разделить выдачу `SEARCH_LIMIT_REACHED` в текущем локальном GPX planner и
OSM application, чтобы оператор видел конкретный исчерпанный бюджет.
Основание: P2-3 в `docs/reviews/2026-11-18-algorithm-docs-review.md`.

## Scope и решения

- Переиспользовать `RECONSTRUCTION_INTERVAL_LIMIT_EXCEEDED` для
  `number >= maximum_reconstruction_intervals` (zero-based gap index).
- Добавить `GAP_RECORD_LIMIT` для `record_count >` соответствующего GPX/OSM cap.
- Добавить `ANCHOR_CANDIDATE_LIMIT` для числа deduplicated matches `>` cap.
- Добавить `PATH_EVALUATION_LIMIT` для нового uncached path при `evaluated_paths >=` cap.
- Передавать конкретный reason через существующий `_LimitReached`.
- Сохранить старый `SEARCH_LIMIT_REACHED` enum для исторических данных;
  текущие места его выдачи перевести на конкретные причины.
- Сохранить automatic OSM marker `gap_record_limit` и накопленные причины GPX.
- Сохранить форму отчётов Core 0.1 / Web 5; документировать расширение словаря.

Затронуты модель reconstruction, local planner, automatic/confirmed OSM application,
регрессионные тесты, flow, каталог алгоритма и review. Новые thresholds, изменение
детекции/выбора маршрута/записи FIT, retries, UI и миграция старых отчётов вне scope.
Context-window caps не получают reason автоматически: это иное условие.

## Acceptance criteria

- [x] Все прежние места выдачи общего reason возвращают конкретное значение.
- [x] Точные сравнения и приоритет отказов не изменены; непроверенный кандидат не пишется.
- [x] Record limit одинаково именуется в GPX, automatic OSM и confirmed OSM.
- [x] Сохранены previous reasons, source bytes, timestamps и masks.
- [x] JSON, HTML и public Web report передают новые reason values; legacy enum читается.
- [x] Обновлены flow, документация совместимости, snapshot и статусы ревью.
- [x] Проверки проекта выполнены; изменения закоммичены, запушены и задеплоены.

## Проверка

Сначала существующие тесты изменены на конкретные reasons: получены ожидаемые
падения на старом коде. Добавлены проверки приоритета interval/record limits,
точной record boundary и сериализации Core JSON/HTML и public Web JSON.

Команды:

```sh
sh scripts/test-all.sh
.venv/bin/mypy src/warpbuster
.venv/bin/python scripts/export_algorithm.py --check
```

Общий прогон: Core 950 passed / 6 skipped; Web 202 passed / 6 skipped;
OSM Manager 56 passed; OSM Routing 491 passed; JS 34 passed. Итого 1699 Python
и 34 JS теста прошли. Mypy: 70 source files, Ruff check/format и exporter check —
успешно. Существующие 12 skips зависят от условий окружения; новых skips не добавлено.

Релиз: v0.3.7. После деплоя проверяются image
revision, container health, публичные маршруты и установленные reason enums.

## Совместимость и откат

Consumers, использующие закрытый список enum, должны принять четыре конкретных
reason values; для старой группировки их можно отображать в `search_limit_reached`
на стороне читателя. Точные причины старых отчётов не угадываются.

Данные активности не мигрируются. Откат: вернуть предыдущий production image
`focus-hive-site-warpbuster:v0.3.6` и проверить `/health`. Тексты reasons могут
отличаться между старыми и новыми результатами; repair semantics неизменны.
