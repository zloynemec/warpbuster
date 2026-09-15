# Task 012C — Автоматическое GPX + OSM восстановление в вебе

Статус: реализация выполнена 2026-09-15; финальные acceptance-проверки container
offline cache hit и OSM-прогона всех шести приватных пар ожидают локальных snapshots.
Milestone: M11. Зависимости: Core 012E с уточнением G6 (`f5a738a`), OSM Manager,
Routing и существующий web service Task 013. Deployment — отдельная задача.

## 1. Пользовательский результат

Пользователь загружает FIT и GPX в существующую форму:

**FIT → независимая очистка → GPX-first → автоматический OSM → FIT и отчёт.**

Сервис сам получает карту и готовит граф. Пользователь не выбирает маршруты,
graph ID, confidence или способ allocation. Нет selection-файлов и подтверждений.
Сохраняются очередь, URL результата, browser-cookie ownership, скачивание FIT
только владельцем и сроки хранения. Частичный проверенный результат можно скачать.

## 2. Исходное состояние

- `processing.process_job` сейчас строит GPX-план, вызывает writer и формирует
  публичную проекцию. Acquisition/prepare/discovery/OSM application не подключены.
- `Worker.process` уже использует subprocess, но общий timeout 180 с завершает job
  как failed. Нужен отдельный OSM deadline с резервом времени для записи GPX.
- Действующая web-политика: `fill_missing_from_course=True`, invalidation=MEDIUM,
  reconstruction=MEDIUM. Сохранить её; CLI defaults не менять.
- Core 012E уже выбирает и применяет пути. Веб не дублирует ranking/selection.
- Dockerfile собирает Core + Web, но не Manager/ Routing/ Valhalla.
- Публичный `result.json` формируется по allowlist и не является локальным HTML payload.

Scope: orchestration, ресурсы, упаковка, публичный отчёт. Detector, геометрические
ограничения, алгоритмы выбора и FIT semantics не пересматриваются ради веба.

## 3. Pipeline одного job

1. Прочитать и проверить оба входа и лимиты размера/числа records и GPX points.
2. Запустить detector без GPX/OSM. Построить базовый GPX RepairPlan с web-политикой.
   Управляющий процесс сохраняет этот план до завершения OSM.
3. Получить eligible internal gaps с двумя пригодными anchors из того же Core-контракта,
   что использует OSMReconstructionProvider. При необходимости выделить общий read-only
   helper; не копировать eligibility в Web.
4. Если eligible gaps нет — OSM `not_needed`, без сети и подготовки графа.
   Принятый GPX сам по себе не исключает gap из совместного discovery.
5. Рассчитать bounded coverage, получить verified snapshot через Manager,
   подготовить/переиспользовать точный граф через Routing.
6. Выполнить совместное discovery и исходный advisory ranking. Вызвать
   `apply_automatic_osm_routes` с MEDIUM. Ничья GPX/OSM не блокирует принятый GPX.
7. При ошибке OSM сохранить базовый GPX и очистку, добавить stage diagnostic.
   Ошибка отдельного gap не отменяет другие результаты. Фатальная ошибка OSM-сессии
   допускает отказ от незавершённой OSM-части, но не от базового GPX-плана.
8. Выполнить selection итогового плана. Если есть изменения — один вызов
   `write_repaired_fit`, затем validation/CRC/preservation/FIT diff.
9. Атомарно опубликовать отчёт и перевести job в ready после проверки. Ошибка writer
   не маскируется под успех; непроверенный FIT недоступен для скачивания.

Запрещено сначала записывать GPX FIT, а затем повторно исправлять его по OSM.
Timestamps и preserved coordinates сохраняются. Несогласованная GNSS-телеметрия
остаётся advisory evidence; допустим estimated active-time allocation по 012E v2.
Исторический confirmed API в пользовательском flow не используется.

## 4. Coverage и acquisition

- Окно каждого eligible gap строится между реальными anchors с буфером 1000 м.
  Объединять grid cells существующего Manager; не создавать огромный общий bbox
  всей активности и не включать явно повреждённые точки.
- Буфер ограничивает доступную карту, а не определяет corruption. Отсутствие маршрута
  не запускает неограниченное расширение/повторные загрузки.
- V1: один bounded union coverage, verified snapshot и graph ID на job. Превышение
  coverage budget даёт `coverage_limit` и GPX fallback, без случайного выбора части gaps.
  Несколько региональных графов — отдельное улучшение.
- Использовать OsmManager.ensure и существующие manifest/digest/profile/engine checks.
  Неготовый/ошибочный cache entry не считается hit.
- Полный пригодный cache hit работает без сети. Полный verified stale snapshot
  допускается по существующей политике Manager с отметкой stale. Неполное покрытие
  нельзя выдавать за полный snapshot.
- Endpoint задаёт оператор; входные файлы/HTTP-параметры не задают URL, пути manifest
  или аргументы native process. Redirect не обходит allowlist источников.
- Внешнему провайдеру передаётся только область grid cells для OSM-запроса.
  FIT/GPX, timestamps, сенсоры и исходные имена не отправляются. В FAQ описать,
  что сервис запрашивает карту для района восстановления.

## 5. Process isolation и конфигурация

Управляющий процесс хранит базовый план. Acquisition/prepare/native routing работают
через дочерний процесс или управляемую группу процессов с bounded IPC.
Typed RoutingClient adapter соединяет Core с этой границей. IPC имеет проверяемую
схему/размер; stderr не сериализуется в публичный JSON.

Timeout останавливает всю группу, включая valhalla_build_tiles, с последующим reap.
Timeout Python thread/future или убийство только прямого child недостаточны.
Общий job deadline включает все дочерние процессы.

Defaults первого контейнерного профиля — именованные параметры с единицами,
ENV overrides, валидацией и тестами:

| Параметр | Default | Назначение |
|---|---:|---|
| osm_mode | auto | auto / offline / disabled; операторская настройка |
| process_timeout_seconds | 600 с | Hard deadline всего job вместо 180 с |
| base_plan_timeout_seconds | 180 с | Чтение, detector, базовый GPX |
| osm_total_timeout_seconds | 360 с | OSM целиком, включая locks/retries |
| osm_acquisition_timeout_seconds | 90 с | Получение snapshot |
| osm_prepare_timeout_seconds | 180 с | Построение графа |
| osm_routing_timeout_seconds | 90 с | Суммарный routing всех gaps |
| publish_reserve_seconds | 60 с | Writer, verification, публикация |
| osm_coverage_buffer_m | 1000 м | Буфер окон gaps |
| osm_maximum_area_km2 | 250 км² | Площадь union cells, проверка до сети |
| osm_maximum_cells | 64 | Cells одного job |
| osm_maximum_requests | 8 | Все внешние попытки, включая retries |
| osm_maximum_download_bytes | 128 MiB | Суммарная загрузка одного job |
| osm_cache_quota_bytes | 10 GiB | Datasets + graphs |
| osm_job_temp_quota_bytes | 2 GiB | Незавершённые acquisition/build |
| osm_minimum_free_bytes | 1 GiB | Резерв диска для публикации результата |
| osm_child_memory_limit_bytes | 2 GiB | Память OSM process group в Linux |
| osm_child_cpu_seconds | 300 с | CPU budget OSM-части |
| maximum_parallel_osm_jobs | 1 | Сохранить текущий одиночный worker |

Эффективный deadline — минимум stage budget и оставшегося общего времени.
Не начинать/не продолжать OSM, если это расходует publish reserve. Невалидные
соотношения таймаутов отклоняются при старте. Лимиты Core/ Routing применяются
дополнительно. Проверять disk budget во время записи/build, не только перед запуском.

Linux-контейнер должен реально ограничивать CPU/RAM всей OSM-группы. OSM OOM не
должен убивать управляющий процесс с базовым планом. Если необходимая изоляция
недоступна, отключить OSM с диагностикой вместо запуска без лимитов.
Ресурсные defaults — стартовые значения; настройка оператором не меняет Core thresholds.

## 6. Кэш, restart и cleanup

- Существующие uploads/results сохраняются. Отдельные каталоги:
  `/data/osm/datasets`, `/data/osm/graphs`, `/data/osm/tmp`, locks/leases.
  Ни один из них не обслуживается HTTP/static endpoints.
- Graph identity включает snapshot digest, профиль и engine/version. Не заменять
  exact graph ID «похожим» графом при ошибке.
- Публикация immutable entries атомарная; незавершённый build/.tmp не READY.
- Переиспользовать locks Manager/ Routing, дополнить leases активных datasets/graphs.
  Eviction не удаляет используемый entry, его blobs и зависимости.
- Удалять старые неиспользуемые entries с учётом ссылок manifests/graphs. Невозможность
  освободить место даёт `cache_quota` и GPX fallback.
- Restart переиспользует verified cache. Cleanup проверяет живые процессы перед
  удалением stale temp/lease. Незавершённые jobs сохраняют текущий статус interrupted.
- Graceful stop завершает дочерние группы и cleanup за ограниченное время.
  Новый экран отмены и распределённая очередь не входят в scope.

## 7. Статусы и публичный результат

Состояния очереди queued/processing/ready/failed/expired сохраняются.
OSM failure — предупреждение, а не обязательная ошибка всего job.
partial определяется оставшимися gaps, а не самим наличием OSM warning.

| Ситуация | Итог |
|---|---|
| Всё восстановлено GPX/OSM | ready, repaired, partial=false, проверенный FIT |
| Есть изменения и остались gaps | ready, repaired, partial=true, частичный FIT |
| GPX восстановил всё, OSM недоступен | ready, repaired, partial=false, предупреждение OSM |
| Исправление не нужно | ready, unchanged, без искусственного нового FIT |
| Есть gaps, но нет применимых изменений | ready, unresolved, без FIT |
| Неверный вход, общий deadline, ошибка writer/validation | failed, без доступного FIT |

Расширить allowlisted публичный payload до schema_version=3:

- counts применённых GPX/OSM/unresolved gaps и filled/unresolved points.
  unresolved_points учитывает все незаполненные gaps, не только invalidated;
- gap: номер, диапазон records, provider gpx/osm/null, action, confidence,
  allocation method, estimated flag, краткие причины из фиксированного словаря;
- OSM status: not_needed/disabled/complete/partial/unavailable; stage и безопасный
  error code. Отсутствие маршрута отличается от ошибки доступа к карте;
- существующие original/corrected/course tracks, метрики, allowlisted FIT diff,
  явная distance uncertainty при несовпадении геометрии и сохранённой телеметрии.

UI показывает «Восстановлено по GPX», «Восстановлено по OSM», «Осталось без
восстановления», понятный partial и приблизительность time allocation.
Сохранить три текущих переключателя карты. Таблица выбора/слои всех альтернатив
не требуются. Schema 2 результаты продолжают отображаться; старые FIT не переписываются.

Полный Core audit, candidate ranking, graph identity, настройки, provenance и полный
FIT diff сохраняются приватно рядом с job, с тем же сроком удаления.
Не публиковать локальный HTML payload, пути, исходные имена/source hashes,
абсолютные timestamps, record telemetry, exception text или native stderr.
Разрешённая публичная геометрия и агрегаты остаются в явной allowlist.
Знание UID не даёт доступ к FIT: server-side cookie ownership на каждом скачивании.

## 8. Наблюдаемость и контейнер

Приватный event journal: base plan, coverage, acquisition, prepare, discovery,
application, write, publication. Поля: job/pair correlation, gap number, duration,
cache hit/miss/stale, bounded counters, policy version и error code.
Новые operational logs не содержат координат/запросов/ответов, исходных имён и tokens.
Точная геометрия/provenance остаются в приватном audit, не в event log.
Сохранить ротацию и корректную обработку ошибки логирования.

Docker собирает Core, Web, оба companion packages и совместимый закреплённый
Valhalla runtime/toolchain. Не полагаться на Homebrew/файлы ноутбука или latest.
Проверять версию runtime при старте и включать её в graph identity.
Сохранить non-root, read-only root filesystem, persistent /data, healthcheck.
Readiness не зависит от доступности OSM endpoint: GPX работает при сетевых сбоях.

Runbook: сборка/локальный запуск, ENV/defaults, CPU/RAM/disk, cache maintenance,
offline, restart/cleanup, timeout diagnosis, rollback через osm_mode=disabled.
Production secrets, DNS, серверный Compose и deployment в scope не входят.

## 9. Acceptance criteria

- [ ] Два файла → проверенный FIT/отчёт без graph ID и ручного выбора.
- [ ] GPX-first/OSM decisions совпадают с Core 012E при одинаковых inputs, graph
  и threshold configuration. Учтено различие MEDIUM web и HIGH CLI invalidation.
- [ ] Synthetic integration: GPX-only, OSM fallback, оба источника, no candidates,
  tied OSM scores, distance mismatch + active time, no-op, partial, writer refusal.
- [ ] Failure matrix: coverage/no coverage, offline miss, network timeout,
  corrupt cache/manifest, engine mismatch, native hang, disk quota, CPU/RAM limit.
  Применимые GPX/очистка сохраняются при OSM failure.
- [ ] Native child и grandchild завершаются по deadline; нет orphan process,
  занятого lease и доступного непроверенного FIT. Publish reserve работает.
- [ ] Concurrent cache requests используют один verified entry. Active leases,
  eviction, restart, interrupted build и graceful stop покрыты тестами.
  Web concurrency при этом не увеличивается.
- [ ] Privacy/ownership tests охватывают public JSON/HTML/downloads/logs;
  schema 2 остаётся совместимой, private Core audit не публикуется.
- [ ] Container smoke: собственный runtime, offline cache hit, persistent mount,
  enforced limits/cleanup, запись FIT и OSM timeout с GPX fallback.
  Network tests используют local fake endpoint, не зависят от живого Overpass.
- [ ] Повторены все шесть реальных пар 012E с локальными snapshots.
  Зафиксированы web/CLI thresholds, источники по gaps, различия, FIT diff/validation
  и hashes. При исходной политике 012E G6 Andromeda восстанавливается по GPX
  с estimated active-time allocation без distance veto.
- [ ] Core, companion, web Python/JS suites и применимые lint/type checks зелёные.
  Runbook/FAQ обновлены. Production deployment не выполнен.

Проверки из корня в установленном dev environment:

```bash
PYTHONPATH=src:packages/osm-routing/src:packages/osm-manager/src:web/backend python -m pytest tests web/tests -q
node --test web/tests/*.test.mjs
python -m ruff check src tests web/backend web/tests packages
PYTHONPATH=src:packages/osm-routing/src:packages/osm-manager/src python -m mypy src/warpbuster
```

Companion suites — отдельно из packages по их README. При реализации добавить
документированные команды container smoke и проверки новых typed adapters.

### Отчёт реализации 2026-09-15

Реализованы GPX-first orchestration, bounded OSM acquisition/prepare/discovery через
Manager и Routing, применение решения через Core 012E, единичная запись итогового FIT,
процессная изоляция с deadline/resource watchdog, cache leases/eviction, schema 3,
публичная allowlist-проекция, UI/FAQ, Docker runtime и GPX fallback.

Автоматические проверки покрывают synthetic/native offline pipeline, ошибки coverage,
cache quota, temp quota, stage timeout и process-group cleanup, lease-safe eviction,
сохранение GPX при OSM failure, публичную схему и шесть приватных FIT/GPX пар в
OSM-disabled fallback. Все исходные private hashes сохраняются.

Не выполнялись production deployment и новые repair heuristics, DEM/altitude или
интеграции с внешними сервисами. Полная повторная проверка шести приватных пар именно
с OSM и container offline cache-hit не заявляются: требуемых локальных snapshots рядом
с private fixtures нет. Native offline cache hit и container runtime/health проверены
раздельно.

## 10. Файлы и порядок реализации

Ожидаемые изменения:

- web/backend/warpbuster_web/{processing,worker,config,store,events}.py;
- новые osm_pipeline.py и osm_worker.py в том же пакете для orchestration/IPC/lifecycle;
- минимальные helper/API additions в Core reconstruction/osm.py и companion packages
  для общих eligibility, leases/limits, без нового алгоритма восстановления;
- web/dist/assets/{result,result-map}.mjs, страницы результата/FAQ по необходимости;
- web/tests и Core/companion tests для затронутых контрактов;
- Dockerfile, web/pyproject.toml, web/README.md, README/roadmap/task report.

Порядок: конфигурация/typed orchestration → process/deadline/fallback →
verified coverage/cache/leases → публичная проекция/UI → Docker/failure tests/runbook →
regression шести пар и локальный browser/container acceptance.

Вне scope: ручной выбор, новые repair heuristics для оставшихся gaps, DEM/altitude,
Garmin/COROS/Strava integrations, login, distributed workers, production deployment.
