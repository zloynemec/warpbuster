# Task 010F — Graph / Valhalla Version Guard

Статус: завершена 2026-09-03; объём сокращён пользователем до проверки версии.

Предыдущая задача: [010E — Alternatives + Route Diagnostics](010e-alternatives-route-diagnostics.md).
Следующий этап: M10 / Task 011A — OSM reconstruction candidates/dry-run.

## Цель

Не выполнять routing по графу, собранному другой версией Valhalla.

Cache key уже содержит версию движка: новый `prepare` под другим runtime получает
другой `graph_id`. Но пользователь может передать старый ID напрямую в `route`.
Проверка целостности файлов и совместимости trail profile не заменяет сравнения
конкретной пары graph/runtime.

## Единственное изменение поведения

В общем входе `RouteService._setup`, после проверки graph integrity и legacy schema,
но **до создания Actor**, сравнить:

- `graph.cache_key.runtime.valhalla` — версию сборки графа;
- `valhalla.__version__` — версию текущего движка.

Требуется точное совпадение строк, включая suffix. Версии не приводятся к числам,
не обрезаются и не сравниваются по semver range. Identity должна быть непустой строкой
без whitespace; отдельный semver parser не добавляется.

При несовпадении:

- Python API выбрасывает `RoutingError` с кодом `GRAPH_ENGINE_MISMATCH`;
- CLI завершает команду с exit code 2;
- сообщение показывает обе версии и предлагает повторить `prepare` исходного
  snapshot manifest в текущем окружении, затем использовать возвращённый `graph_id`;
- JSON error details содержат `graph_id`, `graph_valhalla_version` и
  `runtime_valhalla_version`.

Missing/invalid build identity даёт controlled `CACHE_CORRUPT`; недоступная или
некорректная версия установленного движка — `VALHALLA_REQUEST_FAILED`. Проверка не
пропускается молча. Legacy graph сохраняет прежний `GRAPH_CAPABILITY_MISSING`.

Правило одинаково действует для `route()` и `alternatives()`, включая CLI
`route --alternates 0/1/2`. При совпадении версии существующие результаты не меняются.
`list` и `inspect` остаются доступны при другом runtime. Граф не удаляется, не
пересобирается и не переписывается автоматически.

Это консервативная граница проверенной комбинации, а не утверждение о физической
несовместимости любых двух разных версий. Версии osmium при routing не сравниваются:
они относятся к materialization, а не к исполняющему routing engine.

## Область изменений

- `packages/osm-routing/src/warpbuster_osm_routing/route_service.py`;
- `packages/osm-routing/tests/test_graph_engine_version.py`;
- package README, этот task и плановые документы M9.

## Не входит в задачу

- clean-wheel installation и новый packaging/end-to-end smoke runner;
- обновление dependencies, package version, cache key или manifest schema;
- workers, hard query timeout, capabilities API, compatibility matrix и benchmarks;
- изменения Core/OSM Manager, reconstruction, DEM и FIT.

Runtime hard timeout остаётся отдельным отложенным вопросом до unattended batch/server
режима или обнаруженного зависания. Эта задача не заявляет server/cross-platform
production readiness и не добавляет новых обязательных этапов перед локальным 011A.

## Acceptance criteria

- [x] Matching versions допускают оба API на synthetic graph с настоящим Valhalla.
- [x] Mismatch и malformed identity дают controlled error до создания Actor.
- [x] CLI JSON/console показывают версии, понятный следующий шаг и exit code 2.
- [x] Legacy diagnostics, `list` и `inspect` сохранены; cache bytes не меняются.
- [x] Routing, OSM Manager и Core regression suites проходят; skips указаны отдельно.
- [x] Routing Ruff/format/mypy и `git diff --check` проходят.
- [x] Реализована только проверка версии, без удалённых из ТЗ пунктов.

Команды проверки из корня проекта:

```bash
.venv/bin/pytest packages/osm-routing/tests/test_graph_engine_version.py
.venv/bin/pytest packages/osm-routing/tests
.venv/bin/pytest packages/osm-manager/tests
.venv/bin/pytest tests
.venv/bin/ruff check packages/osm-routing
.venv/bin/ruff format --check packages/osm-routing
.venv/bin/mypy --config-file packages/osm-routing/pyproject.toml packages/osm-routing/src
git diff --check
```

## Результат проверки

Проверено на macOS arm64, Python 3.14.7, Valhalla 3.8.3 в существующем окружении:

- добавлено **49** cases: exact mismatch, включая suffix и соседнюю patch version,
  missing/malformed build/runtime identity, legacy precedence для обоих API;
- synthetic graph с настоящим Valhalla успешно проходит оба API при совпадении версии;
  подмена runtime identity затем блокирует API/CLI до Actor, сохраняет `list`/`inspect`
  и побайтово неизменный cache;
- routing: **292 passed**; OSM Manager: **56 passed**; Core: **218 passed, 5 skipped**;
- пять skips относятся к отсутствующим private `Andromeda_2026.fit` и
  `Andromeda_2026_FIXED.fit`; они не считаются пройденными проверками;
- Ruff check/format, mypy и diff check проходят.

Acceptance criteria выполнены. Production code изменён только в `RouteService`;
новые packaging smoke, workers, schema и изменения Core/Manager не добавлены.
Ограничение остаётся намеренно строгим: другая строка версии требует нового `prepare`,
даже если бинарная совместимость движков теоретически возможна.
