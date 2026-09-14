# Task 012C — Hybrid Reconstruction in Web Production

Статус: подготовлена; реализация отдельно после acceptance 012B и
[010G — Direction-safe Trace Audit](010g-direction-safe-trace-audit.md).
Milestone: M11. Deployment требует отдельного запроса пользователя.

## Уточнение очередности после 010I

После 012D выполнить [012E — GPX-first и автоматическое применение OSM](012e-automatic-osm-application.md).
Первая версия: загрузить FIT и GPX, получить исправленный FIT. Ручного выбора
и подтверждения маршрутов нет. Discovery собирает GPX и OSM совместно; GPX-first
относится к применению. Межпровайдерная ничья ranking не блокирует принятый GPX.
Автоматический OSM fallback и application policy реализуются в Core 012E.

## Scope

Подключить принятый Core-контракт GPX-first → OSM fallback к web worker.
Не менять detector, правила confidence/selection или Core write scope ради UI.

1. OSM Manager: вычислять bounded coverage для eligible internal gaps совместного discovery,
   acquisition через публичный API, allowlisted источники, проверка manifest/digest,
   атомарный cache, дедупликация параллельных acquisition, quota и eviction без
   удаления используемых snapshots. Никаких приватных FIT/GPX в публичном cache.
2. Routing: prepare только verified manifest; cache graph identity включает snapshot,
   profile, engine/version. Передавать точный graph ID и все diagnostics в Core.
3. Persistent cache mount вне container image; отдельные директории для immutable
   datasets/graphs и временных файлов. Права минимальны; offline cache hit не
   запускает acquisition. Учитывать restart и interrupted build.
4. Resource limits: отдельный worker process для native Valhalla; wall-clock deadline,
   kill/reap, CPU/RAM/disk/request/coverage/query limits; cancellation и cleanup.
   Python timeout вокруг native call без остановки процесса недостаточен.
5. Docker: упаковать OSM Manager, Routing и совместимый pinned Valhalla runtime;
   reproducible build, non-root runtime, health/readiness, smoke tests без network.
6. Logging: job/gap correlation, стадия/длительность, cache hit/miss, provenance,
   controlled failure code, selection/refusal reason. Не логировать tokens,
   координаты, исходные имена файлов или route geometry по умолчанию.
7. Graceful failure: acquisition/cache/prepare/routing timeout/ошибка сохраняют GPX
   результат и unresolved diagnostics. Никакой повторной классификации corruption,
   silent lower-confidence apply или частично опубликованного FIT. Writer запускается
   один раз после merge; результат включает FIT diff и preservation validation.
8. Web flow автоматически использует Core 012E: GPX-first, затем выбор и применение
   OSM для оставшихся gaps. Не запрашивать ручной выбор и не создавать фиктивные
   confirmations. Причины автоматического выбора и оставшихся пропусков видны в отчёте.

## Acceptance criteria

- Integration tests: GPX-only; GPX+OSM; unresolved ambiguity; no coverage; download
  failure; corrupt cache; engine mismatch; native timeout; disk quota; cancellation.
- Concurrent jobs используют один verified cache entry; restart переиспользует cache;
  interrupted build никогда не считается READY.
- Container smoke test: bundled runtime, persistent mount, offline cache hit, enforced
  process timeout и cleanup; ни один failure не повреждает оригинал или GPX результат.
- JSON/HTML содержат stage errors, decision/provenance и полный FIT diff; user
  получает явный partial/unresolved результат. Privacy tests охватывают logs/cache.
- Все Core, companion package и web tests, lint/type-check проходят.
- Runbook описывает размер cache, capacity, limits, rollback и graceful degradation.
- Deployment не выполняется в этой задаче без отдельного запроса пользователя.
