# WarpBuster — код-ревью

**Дата:** 16 ноября 2026
**Объект:** весь репозиторий (`src/`, `web/backend/`, `packages/osm-manager`, `packages/osm-routing`, `tests`, `docs`)
**Основание:** документация внутри репозитория (`AGENTS.md`, `README.md`, `CODEX_WORKFLOW.md`, `docs/ARCHITECTURE.md`, `docs/PRODUCT_SPEC.md`, `docs/DECISIONS.md`, `docs/CLI_SPEC.md`, `docs/DETECTION_MODEL.md`, `docs/TEST_STRATEGY.md`, `web/README.md`)
**Статус:** ревью без изменений кода. Рабочее дерево не тронуто (есть только untracked `.hallmark/`-каталоги).

---

## 1. Резюме

Архитектурно проект в хорошей форме. Главный инвариант — «never modify plausible movement / repair only demonstrably impossible GNSS data» — соблюдён **в коде**, а не только на бумаге:

- `src/warpbuster/integrity/` не содержит ни одного упоминания course/OSM/DEM;
- в `src/` нет `random`, `datetime.now`, ML/LLM и сетевых вызовов (кроме DEM-стадии реконструкции);
- `fit/writer.py` публикует без перезаписи через `os.link`, патчит байты и сохраняет неизвестные поля;
- `report/html.py` экранирует `<`, `>`, `&`, U+2028/2029 в embedded JSON;
- timestamps не изменяются, `RepairPlan.timestamps_unchanged` отражает это явно;
- на текущей perf-фикстуре целевое время выполняется: 20 000 records ≈ 2.6 s суммарно.

Найденные проблемы — в основном периферия: web-конфиг, валидация конфигов, обработка ошибок/ресурсов, производительность OSM-пакетов и course-поиска. **Дефектов детекции не найдено.** В реконструкции два вопроса требуют решения до реальной эксплуатации: R1 (линейный поиск анкоров, до 126 s в худшем замере на 20k×20k) и R4 (`READY` при MEDIUM-кандидатах противоречит ADR-018).

| Область | high | medium | low/nit | Всего |
|---|---:|---:|---:|---:|
| Ядро (`src/`) + web | 1 | 7 | 4 | 12 |
| OSM-пакеты | 2 | 3 | 6 | 11 |
| Reconstruction (`src/warpbuster/reconstruction`) | 1 | 4 | 4 | 9 |
| **Итого** | **4** | **14** | **14** | **32** |

### Индекс находок

| ID | Severity | Область | Суть |
|---|---|---|---|
| P1-1 | high | web | IPv6-loopback origin разрешён, но все запросы получают 400 |
| P2-1 | medium | core | `IntegrityConfig` принимает NaN/Inf для порогов |
| P2-2 | medium | core | `except Exception` вокруг OSM-стадии маскирует баги |
| P2-3 | medium | web | `EventLog.write` может уронить вызывающий поток |
| P2-4 | medium | web | `worker.process` не защищён от `None` из store |
| P2-5 | medium | process | Нет CI; корневой `pytest` не запускает web и packages |
| P2-6 | medium | process | `mypy strict` объявлен, но не проходит и не покрывает web |
| P2-7 | medium | docs | Устаревшее дерево модулей в `docs/ARCHITECTURE.md` |
| P3-1 | low | web | ASCII-decode `raw_path`/`query_string` → 500 |
| P3-2 | low | CLI | `--json`/`--gpx` публикуются не атомарно парой |
| P3-3 | low | FIT | `os.link` + `unlink` без fsync: возможен скрытый `.tmp` |
| P3-4 | low | docs | «core offline» читается шире, чем есть (DEM ходит в сеть) |
| O1 | high | osm-routing | N+1 SQL при валидации ссылок (до 50M запросов) |
| O2 | high | osm-routing | Каждый route-запрос перехеширует все тайлы графа |
| O3 | medium | osm-manager | `stale` не входит в identity снапшота |
| O4 | medium | osm caches | Stale lock не перехватывается → вечная блокировка |
| O5 | medium | osm-manager | Redirect в Overpass проверяется после записи тела |
| O6 | low | osm-manager | `read_manifest` падает `AttributeError` на не-объекте |
| O7 | low | osm-manager | Существующий blob публикуется без перехеширования |
| O8 | low | osm-manager | `_read_bounded` доверяет `st_size` (FIFO/рост файла) |
| O9 | low | osm-manager | Нет защиты от `--cache-dir /` или `~` |
| O10 | nit | osm-routing | Мёртвая проверка `sac_scale`, неиспользуемый параметр |
| O11 | nit | osm-routing | Дублирование константы `25_934_402` |
| R1 | high | reconstruction | `_CourseIndex.matches` линейно сканирует все рёбра course |
| R2 | medium | reconstruction | `_options` перебирает пары анкоров без бюджета |
| R3 | medium | reconstruction | `_stable_anchor` игнорирует переданный конфиг |
| R4 | medium | reconstruction | `READY` при MEDIUM-кандидатах противоречит ADR-018 |
| R5 | medium | reconstruction | Возможен HIGH при почти нулевом span реконструкции |
| R6 | low | reconstruction | `preserve_recorded_distance` теряется для prefix/suffix |
| R7 | low | reconstruction | Off-by-one в лимите DEM-профилей |
| R8 | low | reconstruction | `safety._has_position` принимает NaN/inf; модуль не подключён |
| R9 | low | reconstruction | Hardcoded MEDIUM в endpoint DEM; глобальный `total_points` |

---

## 2. Методика и охват

Прочитаны документация, ядро (`config`, `models`, `integrity/*`, `fit/*`, `pipeline/*`, `reconstruction/*`, `report/*`, `cli.py`), web-backend, оба OSM-пакета, наборы тестов и ключевые фикстуры. Дополнительно запущены тесты, линтер, mypy и замер производительности.

### Запущенные проверки

| Проверка | Команда | Результат |
|---|---|---|
| Core tests | `.venv/bin/python -m pytest -q tests` | **848 passed, 6 skipped** (~159 s) |
| OSM manager | `cd packages/osm-manager && ../../.venv/bin/python -m pytest -q tests` | **56 passed** |
| OSM routing | `cd packages/osm-routing && ../../.venv/bin/python -m pytest -q tests` | **491 passed** (~22 s) |
| Web | `PYTHONPATH=web/backend:. .venv/bin/python -m pytest -q web/tests -p no:cacheprovider` | **178 passed, 5 skipped** (~181 s) |
| Lint | `.venv/bin/python -m ruff check src tests web/backend packages/*/src packages/*/tests` | **All checks passed** |
| Perf 20k | `.venv/bin/python -m pytest -q -s tests/test_local_reconstruction_performance.py` | normalize 0.738 s, detect 0.393 s, plan 0.403 s, html 1.085 s |
| Recon tests | `.venv/bin/python -m pytest -q tests -k "reconstruction or missing_course or endpoint or dem or safety"` | **240 passed, 614 deselected** (~79 s) |
| Benchmark R1 | синтетический `_CourseIndex` (плоский course) | 5 000 рёбер × 5 000 записей = **7.9 s** → 20k × 20k ≈ **126 s** |
| Instrument R1 | `build_repair_plan` на perf-фикстуре | 20k course, `matches_calls=40`, `matches_time=0.228 s`, `plan=0.357 s` |
| mypy `src` | `.venv/bin/python -m mypy src` | чисто |
| mypy `src tests` | `.venv/bin/python -m mypy src tests` | 811 ошибок в 45 файлах |
| mypy c web | `.venv/bin/python -m mypy src web/backend packages/*/src` | 121 ошибка в 16 файлах |

Важные оговорки по запуску:

- корневой `pyproject.toml` задаёт `testpaths = ["tests"]`, поэтому `pytest` из корня **не** запускает `web/tests` и `packages/*/tests`;
- web-тесты требуют `PYTHONPATH=web/backend:.` (они импортируют core `tests.*`), из каталога `web/` не собираются;
- package-тесты требуют запуска из собственного каталога пакета;
- mypy-ошибки в тестах/web не являются регрессией этого ревью: конфиг `strict` в корне фактически покрывает только `src`.

### Границы охвата

- Core и web-backend — построчный обзор.
- `reconstruction/*` — построчный обзор планирования, выбора и применения; дефекты R1–R9 подтверждены по коду, R1 — воспроизведённым замером.
- OSM-пакеты — построчный обзор с воспроизведением ключевых дефектов.
- `tests/private/` (38 приватных FIT) просмотрены только на предмет формата; в репозиторий не попадают.

---

## 3. P1 — блокирующее для web-эксплуатации

### P1-1. IPv6-loopback origin разрешён, но сервер отвечает 400 на всё

**Файлы:** `web/backend/warpbuster_web/config.py:95`, `web/backend/warpbuster_web/app.py:553`

Конфиг явно разрешает IPv6-loopback:

```python
if origin.scheme != "https" and origin.hostname not in {"localhost", "127.0.0.1", "::1"}:
```

но middleware получает `allowed_hosts=[urlsplit(origin).hostname]`, то есть `["::1"]`. `TrustedHostMiddleware` из Starlette режет `Host` по первому `:`:

```text
Host: [::1]:8000  →  "["  →  нет в allowed_hosts  →  400 Invalid host header
```

**Воспроизведение:** прямой ASGI-вызов.
`origin=http://127.0.0.1:8000` → `/health` = 200; `origin=http://[::1]:8000` → `/health` = **400**.

**Почему это P1:** это валидная по собственному валидатору конфигурация. Сервер стартует, пишет в лог нормальный URL и полностью не отвечает — включая `/health`, то есть без диагностики.

**Исправление:** передавать в `allowed_hosts` bracketed-форму (`"[::1]"`) или нормализовать хост так, как его присылает клиент.

**Тест:** `public_origin="http://[::1]:8000"` → `/health` = 200. Сейчас IPv6 покрыт только в live-acceptance как допустимый hostname, но не как рабочий origin.

---

## 4. P2 — существенные, не блокирующие

### P2-1. `IntegrityConfig` принимает NaN/Inf — асимметрия валидации порогов

**Файл:** `src/warpbuster/config.py:152–214`

Все проверки имеют вид `if value <= 0`. Для `float("nan")` это `False`, поэтому NaN **проходит**. Проверено:

- `IntegrityConfig(relative_speed_multiplier=nan)` — принят;
- `IntegrityConfig(relative_speed_multiplier=inf)` — принят;
- `IntegrityConfig(absolute_impossible_speed_mps=nan)` — принят;
- `IntegrityConfig(geometry_max_path_to_chord_ratio=nan)` — принят;
- `tail_position_error_budget_m=inf` — отвергнут (там проверка другая).

`PipelineConfig`, `CourseReconstructionConfig`, `GpsAnalysisConfig` используют `math.isfinite` и такие значения отвергают.

**Почему важно:** NaN в пороге — это не «чуть неверный порог», а тихое отключение детекции. Любое сравнение с NaN даёт `False`, переходы перестают классифицироваться как невозможные → false negatives. Это ломает воспроизводимость и правило AGENTS.md §9. Сейчас путь только программный (CLI строит `IntegrityConfig` из констант), поэтому P2.

**Исправление:** `not math.isfinite(value) or value <= 0` для всех float-порогов, включая optional.

**Тест:** параметризованный тест на NaN/±Inf для каждого поля.

### P2-2. Широкий `except Exception` вокруг опциональной OSM-стадии

**Статус на 2026-11-19:** исправлена потеря приватной диагностики с сохранением
безопасного fallback. `osm_failure()` пишет bounded type/reason/traceback в
`OSMResult.private_audit`; Web сохраняет его локально с mode 0600, без HTTP-выдачи.
Broad catches намеренно сохранены, включая boundary изолированного child.
Предложение ниже о пробрасывании неожиданных ошибок остаётся исторической
альтернативой и не применяется: оно отменило бы безопасный базовый GPX-результат.
Регрессии проверяют byte-identical GPX fallback, chaining, сломанный formatter,
UTF-8, permissions и отсутствие диагностических строк в публичных ответах.

**Файл:** `src/warpbuster/pipeline/repair.py:187–189`

```python
except Exception:
    osm = OSMResult(base_plan, "unavailable", "routing", "routing_failed")
```

Комментарий говорит «optional companion/native failures», но `Exception` ловит `MemoryError`, `AttributeError`/`TypeError` из-за реального бага и любые ошибки программирования внутри `run_osm_pipeline`. Баг в необязательной стадии навсегда маскируется под `routing_failed` и не виден ни в тестах, ни в отчёте как дефект.

**Исправление:** ловить конкретные типы (ошибки OSM/routing/timeout), остальное пробрасывать; либо логировать `exc_info` в диагностику.

**Тест:** ожидаемый тип → `unavailable`; неожиданный → наружу.

### P2-3. `EventLog.write` может уронить вызывающий поток

**Файл:** `web/backend/warpbuster_web/events.py:20–33`

`json.dumps(..., allow_nan=False)` стоит **до** `try`. Любой несериализуемый или нефинитный `detail` даёт `TypeError`/`ValueError` прямо из `write`, а вызывается он из обработчиков и воркера. Сейчас все вызовы передают безопасные значения, но это скрытая мина: логирование не должно ломать бизнес-операцию.

**Исправление:** перенести сериализацию внутрь `try` и деградировать до усечённого события (или `repr`), не пробрасывая исключение.

### P2-4. `worker.process` не защищён от `None` из store

**Файл:** `web/backend/warpbuster_web/worker.py:65`

```python
has_course = bool(self.store.get(uid)["has_course"])
```

находится вне `try`. Если запись истекла/удалена между постановкой в очередь и обработкой — `TypeError: 'NoneType' object is not subscriptable` в потоке воркера, задача навсегда останется `pending`. Сейчас гонка маловероятна (один поток и порядок expiry), но инвариант «job всегда терминален» этим не защищён.

**Исправление:** `record = self.store.get(uid)`; при `None` — пометить job failed и выйти.

**Тест:** `process` при `store.get(uid) is None`.

### P2-5. Нет CI, а корневой `pytest` даёт ложнозелёный результат

В репозитории нет `.github/workflows` и нет `Makefile`-цели, запускающей все наборы. `testpaths = ["tests"]` означает, что `pytest` из корня не запускает `web/tests` и `packages/*/tests`. AGENTS.md §12 требует обязательных тестов, но фактически это держится только на дисциплине: «зелёный» корневой прогон не означает, что web и пакеты живы.

**Исправление:** один скрипт/CI-job с четырьмя командами из §2 + `ruff`; это же зафиксирует требование к `PYTHONPATH`.

### P2-6. `mypy strict` объявлен, но не проходит и не покрывает web

`[tool.mypy] strict = true` в корне, при этом:

- `src tests` → 811 ошибок в 45 файлах (нетипизированные тесты/фабрики);
- `src web/backend packages/...` → 121 ошибка в 16 файлах (у web-backend нет своего mypy-конфига).

Либо это осознанно («strict только для `src`»), и тогда нужен `files = ["src"]`, либо это долг. Сейчас конфиг создаёт ложное впечатление типовой чистоты. Отдельно: `src` действительно чист.

### P2-7. Устаревшее дерево модулей в `docs/ARCHITECTURE.md`

**Файл:** `docs/ARCHITECTURE.md:40–73`

Из перечисленных модулей **не существует 8**: `fit/preserve.py`, `integrity/transitions.py`, `integrity/reachability.py`, `integrity/scoring.py`, `geo/trajectory.py`, `reconstruction/base.py`, `report/console.py`, `report/json.py`.

Не перечислены реально существующие: `integrity/one_sided.py`, `tail.py`, `odometer.py`, `geometry.py`, `vertical.py`, `reconstruction/{local,gaps,safety,endpoints,selection,orchestration,osm_application,automatic_osm,candidate_ranking,approximate_contract,dem_choice}.py`, `report/{analyze,fit,gaps,inspect,repair}.py`.

Формально в документе есть оговорка «структура может уточняться», но для проекта, где документация объявлена источником требований, это сбивает с толку при первом чтении.

---

## 5. P3 — мелочи и устойчивость

### P3-1. ASCII-decode в `CanonicalHostMiddleware`

**Файл:** `web/backend/warpbuster_web/app.py:87–88`

`raw_path`/`query_string` декодируются как `ascii`. На не-ASCII байтах в запросе получится `UnicodeDecodeError` → 500 вместо 400/редиректа. Обычные клиенты шлют percent-encoded ASCII, поэтому вероятность низкая.

**Исправление:** `errors="surrogateescape"` или использовать уже распарсенный `scope["path"]`.

### P3-2. `--json`/`--gpx` публикуются не атомарно парой

**Файл:** `src/warpbuster/cli.py:846–855`

`_write_gps_outputs` публикует артефакты пофайлово; если второй `os.link`/`os.replace` упадёт, первый файл уже опубликован. Это честно написано в комментарии, но в `docs/CLI_SPEC.md` не отражено. Либо сделать пару атомарной (stage → publish с откатом), либо задокументировать поведение.

### P3-3. Публикация FIT без fsync каталога

**Файл:** `src/warpbuster/fit/writer.py:176–179`

Публикация через `os.link` + `unlink` временного файла; падение между ними оставляет скрытый `.tmp`, directory fsync отсутствует. Для CLI приемлемо, для серверного сценария стоит закрыть.

### P3-4. «Core offline» читается шире, чем есть

**Файл:** `src/warpbuster/pipeline/dem.py:10`

DEM-стадия использует `urllib.request`. Ядро (детектор) действительно offline, но формулировку стоит уточнить в доках: «detection offline; DEM-стадия может ходить в сеть при незаполненном кэше».

---

## 6. OSM-пакеты (`osm-manager`, `osm-routing`)

Оба набора тестов зелёные (56 + 491), но в не-тестовых путях есть два серьёзных дефекта производительности/поведения.

### O1. high — N+1 SQL при валидации ссылок OSM

**Файл:** `packages/osm-routing/src/warpbuster_osm_routing/normalize.py:266` и `:285`

```python
for node_id in document["nodes"]:
    exists = connection.execute(
        "SELECT 1 FROM visible_ids WHERE kind='n' AND object_id=?", (node_id,)
    ).fetchone()
```

Один запрос на каждую ссылку way и на каждого члена relation при лимите `maximum_total_node_references = 50_000_000` (`config.py:100`). При ~27k запросов/с (замер: 1.86 s на 50k) реальный экстракт — минуты–часы, прямое нарушение AGENTS.md §11. Индекс `visible_identity` есть, но он не помогает против round-trip на строку.

**Исправление:** одно `EXCEPT`/`JOIN` на таблицу (или батчи `IN (...)`), примеры ошибок собирать после set-сравнения и ограничивать `maximum_diagnostic_items` постфактум.

**Тест:** `test_performance.py` строит источник только из nodes, поэтому путь не проверяется на масштабе — нужен ways+refs кейс с жёстким бюджетом.

### O2. high — каждый route-запрос перехеширует весь граф

**Файлы:** `route_service.py:117` → `graph_cache.py:347–407`

`RouteService._setup` вызывает `self.cache.inspect(...)` на каждый запрос (включая alternatives). `inspect` → `_verify_directory`, а тот:

- `_verify_file` для `source.osm.pbf` и `valhalla.json` — полный SHA-256;
- `_inventory_tiles` → `_hash_file` для **каждого** файла тайлов, при лимитах `maximum_tile_files` (250k) и `maximum_total_tile_bytes` (8 GiB).

Латентность запроса линейна по размеру графа и легко усиливается внешним клиентом (каждый вызов — гигабайты чтения). Инвариант целостности правильный, но проверять его на каждом чтении не нужно.

**Исправление:** верифицировать один раз при prepare/publish и кэшировать маркер (manifest digest + mtime/size, либо отдельный `verified.json`); полную проверку оставить в `inspect --verify`/CLI.

### O3. medium — `stale` не входит в identity снапшота

**Файл:** `packages/osm-manager/src/warpbuster_osm_manager/cache.py:307–332`

`identity` включает профиль, coverage, fingerprint, `manager_settings` и cells, но **не** `stale`. Reuse-путь (`:329–332`) просто читает существующий `manifest.json`. Воспроизведено: offline-`ensure` создаёт `"stale": true`, последующий успешный `refresh=True` возвращает `EnsureResult.stale=False`, а манифест на диске остаётся `stale: true` с тем же id.

**Исправление:** включить `stale` в identity либо не переиспользовать `stale=True` манифест для свежего результата; `EnsureResult.stale` вычислять из манифеста, а не из аргумента.

**Тест:** «stale-манифест + refresh → новый манифест / согласованный флаг».

### O4. medium — stale lock не перехватывается (асимметрия с менеджером)

**Файлы:** `graph_cache.py:499–534` (`_GraphLock`), `dem_cache.py:546–573` (`_Lock`)

Оба лока при таймауте только вычисляют `age` и кладут `stale` в детали ошибки — перехвата нет; `stale_lock_seconds` фактически декоративен. В manager-кэше recovery **есть** (`cache.py:128`: `age > stale_lock_seconds and not _process_is_alive(...)`). После `SIGKILL`/перезагрузки кэш routing/DEM блокируется навсегда, пока не удалят файл руками.

Дополнительно: `__exit__` делает `unlink()` без проверки владельца/token — при перехвате «старый» владелец удалит чужой лок.

**Исправление:** при таймауте — takeover по возрасту/живости PID; в лок писать token, удалять только свой.

**Тест:** «stale lock → успешный захват», сейчас проверяется только диагностика таймаута.

### O5. medium — проверка redirect в Overpass выполняется после записи тела

**Файл:** `packages/osm-manager/src/warpbuster_osm_manager/overpass.py:140`

`_validate_final_url` вызывается **после** `transport.download(...)`, а `UrlLibTransport.download` (`:63–84`) использует `urllib.request.urlopen`, который по умолчанию следует редиректам и пишет ответ на диск. Контроль «unexpected host» срабатывает постфактум: на 307/308 тело запроса (Overpass QL) уходит на чужой хост, а payload уже лежит в файле. `dem_cache` при этом redirects запрещает (`_NoRedirect`, `dem_cache.py:104`).

**Исправление:** запретить редиректы в транспорте (как в `dem_cache`) либо валидировать `Location` в кастомном handler до чтения тела.

### O6. low — `read_manifest` падает на не-объекте

**Файл:** `cache.py:403`

`document.get(...)` вызывается до `isinstance(document, dict)`, поэтому `[]` в `manifest.json` даёт необработанный `AttributeError` (воспроизведено). Рядом `_referenced_paths` (`:540`) даёт `KeyError` на манифесте без `data_files`.

**Исправление:** проверять тип первым и оборачивать в `_cache_error`.

**Тест:** malformed/non-object `manifest.json`.

### O7. low — существующий blob публикуется без перехеширования

**Файл:** `cache.py:253`

При существующем blob `_publish_file` возвращает его без проверки: обрезанный или подменённый файл с правильным именем публикуется как валидный.

**Исправление:** сверять размер/хеш существующего файла перед reuse.

### O8. low — `_read_bounded` доверяет `stat().st_size`

**Файл:** `coverage.py:48–56`

Файл, выросший после `stat`, или FIFO/спецфайл (`st_size == 0`) обходят `maximum_input_file_bytes` и могут исчерпать память.

**Исправление:** потоковое чтение с явным счётчиком байт.

### O9. low — нет защиты от широкого `--cache-dir`

**Файл:** `cache.py:56`

У менеджера нет аналога `_safe_cache_root` из routing: `--cache-dir /` или `~` создаст `~/blobs`, `~/snapshots`, … прямо в домашнем каталоге.

**Исправление:** переиспользовать проверку из routing (`/` и `$HOME` запрещены).

### O10. nit — мёртвая проверка и неиспользуемый параметр

**Файл:** `route_service.py:655–659`, `:399`

`if not isinstance(sac_scale, int | float) or sac_scale > _MAX_ALLOWED_SAC_SCALE` недостижим после `type(...) is not int or not 0 <= x <= 3`. Параметр `start` в `_route_payload` принимается, но не используется, хотя вызывающие передают audited snap.

**Исправление:** удалить мёртвые проверки/параметр либо реально их применять.

### O11. nit — дублирование константы размера тайла

**Файл:** `dem_cache.py:472`

`expected = 25_934_402` дублирует `SkadiDatasetProfile.uncompressed_tile_bytes` (`dem_profile.py:87`). При изменении профиля проверка тихо разойдётся.

**Исправление:** использовать константу профиля.

---

## 7. Reconstruction (`src/warpbuster/reconstruction`)

Модули планирования и применения реконструкции проверены построчно. Ключевой инвариант «repair only demonstrably impossible» здесь не нарушается, но найдены дефекты производительности, конфигурации и согласованности статусов. Тесты реконструкции зелёные: 240 passed (79 s) на широком подмножестве, 47 passed на 11 целевых файлах.

### R1. high — поиск анкоров линейно сканирует все рёбра course

**Файл:** `src/warpbuster/reconstruction/local.py:116–175`

`_CourseIndex.matches()` для каждого record перебирает **все** рёбра course. Фильтр только по широтному боксу каждого ребра, пространственного индекса нет, лимита на размер course тоже нет (`gpx/course.py` не ограничивает число точек). Кэш хранит результат по `record.index` (до 32 совпадений на запись), но не ограничивает суммарную стоимость.

**Замер (воспроизведено):** плоский course (все точки на одной широте — типичный «восточный» трек, где широтный фильтр не отсекает) 5 000 рёбер × 5 000 записей = **7.9 s**, экстраполяция на 20 000 × 20 000 ≈ **126 s**. Для сравнения: на наклонном course 5 000 × 5 000 = 1.47 s (≈24 s на 20k).

**Почему это не поймал perf-тест:** `tests/test_local_reconstruction_performance.py` использует фикстуру `local_fixture(count=20_000)`, но реально матчится только **40** записей (контексты короткие), поэтому `plan=0.36 s`. Проверено инструментированием `_CourseIndex.matches`: `plan=0.357s, matches_calls=40, matches_time=0.228s`. То есть риск материализуется при длинных контекстах (prefix/suffix, много gap-ов), а не на текущем сценарии.

**Исправление:** сетка/интервальный индекс по широте-долготе (или R-tree), плюс ограниченный LRU-кэш контекстов вместо неограниченного `dict` по всем индексам.

**Тест:** 20k-точечный плоский course + 20k записей с бюджетом; сейчас такого кейса нет.

### R2. medium — перебор пар анкоров без бюджета

**Файл:** `src/warpbuster/reconstruction/local.py:462–510`

`local_alignment_max_path_evaluations = 128` ограничивает только `_allocate_path`. `_options` же перебирает `product(matches(context[0]), matches(context[-1]))` — до 32×32 = 1024 пар, и для каждой выжившей проходит до 300 записей контекста, вызывая `index.position` + `geodesic_distance_m`. Это повторяется на каждом шаге растущего окна (см. `:652–731`), то есть до ~19 раз на gap. Худший случай — миллионы геодезических вызовов на один gap до применения любого бюджета.

**Исправление:** считать/оценивать пары против явного бюджета и отсекать по span до построчного прохода.

**Тест:** синтетический gap с максимальным числом совпадений на обоих анкорах и жёстким верхним пределом вызовов `position`/`geodesic_distance_m`.

### R3. medium — `_stable_anchor` игнорирует конфиг вызывающего

**Файл:** `src/warpbuster/reconstruction/endpoints.py:153` и `:169`

```python
if gap.reasons or gap.record_count > AutomaticOSMApplicationConfig().maximum_gap_records:
...
minimum = CourseReconstructionConfig().anchor_stability_min_normal_transitions
```

Функция создаёт **дефолтные** конфиги вместо переданных. `apply_endpoint_routes` уже принимает `config: AutomaticOSMApplicationConfig` (`:195`) и `cfg` доступен, но в `_stable_anchor` не передаётся; `minimum_confidence` при этом прокидывается корректно.

**Почему важно:** переопределения молча не действуют. Уменьшенный `maximum_gap_records` или профиль с меньшим числом требуемых `NORMAL` переходов могут принять endpoint-gap/анкер, который внутренний путь (`automatic_osm.py:211`) отверг бы.

**Исправление:** прокинуть `cfg`/`CourseReconstructionConfig` через `_stable_anchor`, `endpoint_pairs`, `apply_endpoint_routes`.

**Тест:** переопределённый `maximum_gap_records`/`anchor_stability_min_normal_transitions` реально меняет решение `_stable_anchor`.

### R4. medium — `READY` при MEDIUM-кандидатах противоречит ADR-018

**Файлы:** `src/warpbuster/reconstruction/local.py:274–284`, `orchestration.py:104–115`, `endpoints.py:341–350`, `osm_application.py:249–257`, `automatic_osm.py:491–499`

ADR-018: «Только plan, где все detected intervals имеют HIGH candidates, получает `READY`. Наличие хотя бы одного unresolved или MEDIUM candidate даёт `PARTIAL`».

Фактически `local.py` ставит `READY` при `changes_available and not unresolved`, полностью игнорируя confidence, а `endpoints.py`/`osm_application.py` дополнительно считают mask-`invalidations` «изменениями». При этом `orchestration.py:57–64` (non-local merge) требует `all(c.repair_eligible …)` (это HIGH). Итог: план может быть `READY`, а selection с дефолтным `HIGH` не применяет **ничего**.

Воспроизведение: `tests/test_missing_course_completion.py:50` — два endpoint-кандидата с MEDIUM → `plan.status is READY`. То есть тест **закрепляет** отклонение от ADR-018.

**Почему это важно:** CLI печатает одновременно `Status: READY` и `Repair eligible: no` — прямо противоречивый вывод для пользователя.

**Исправление:** выбрать одну семантику и применить её во всех продюсерах: либо `READY` только при `all(c.repair_eligible)`, либо ввести явную пару «coverage complete» / «applicable at threshold» и обновить ADR-018.

### R5. medium — возможен HIGH при почти нулевом span реконструкции

**Файлы:** `src/warpbuster/reconstruction/local.py:550`, `:786`, `:864–874`

`_paths` для INTERNAL допускает `after.course_distance_m == before.course_distance_m`, поэтому два анкора могут спроецироваться почти в одну точку chainage. Нижняя граница — `path.length <= 0`, то есть несколько метров за минуты проходит. `high` вычисляется так:

```python
high = (
    not gap.original_missing_count
    and not any(item.endswith(("_path_mismatch", "_allocation_implausible", "_zero")) ...)
    and endpoint_source is None
    and gap.invalidation_confidence is HIGH
    and max(anchor distances) <= high_confidence_anchor_distance_m
)
```

Если оба потока недоступны, `_fractions` возвращает `TIMESTAMPS` с диагностиками `distance_unavailable`, `speed_unavailable`, `active_time_estimated` — ни одна не оканчивается на `_zero`, поэтому `high` остаётся `True`, и writer применяет практически стационарную реконструкцию как HIGH.

**Оговорка:** стационарная реконструкция может быть верной (спортсмен стоял). Проблема в том, что HIGH выдаётся без требования минимального span и без учёта `AllocationMethod.TIMESTAMPS`.

**Исправление:** требовать минимальный `path.span`/долю observed distance и понижать confidence при аллокации только по timestamps.

**Тест:** gap с совпадающими chainage и недоступными distance/speed → не `HIGH`.

### R6. low — `preserve_recorded_distance` теряется для prefix/suffix

**Файл:** `src/warpbuster/reconstruction/local.py:881–892`

```python
preserve = not distance_signal.correction_supported
```

Для prefix/suffix любой impossible distance increment в окне, включающем анкеры, даёт `preserve=False`. Тогда writer пересчитывает `record.distance` и lap/session totals из **сгенерированной** геометрии — это прямо запрещено ADR-025: «cumulative distance и summaries не пересчитываются из сгенерированной geometry».

**Исправление:** принудительно `preserve_recorded_distance=True`, когда `gap.original_missing_count > 0`.

**Тест:** prefix/suffix + distance spike → `preserve_recorded_distance is True`.

### R7. low — off-by-one в лимите DEM-профилей

**Файлы:** `src/warpbuster/reconstruction/automatic_osm.py:232`, `dem_choice.py:65–66`

```python
if accepted_for_dem and ... and len(accepted_for_dem) > maximum_profiles_per_gap:
    break
```

Проверка выполняется **до** добавления, поэтому при `len == max` условие ложно и добавляется ещё один профиль (max+1). Затем `compare_dem_candidates` возвращает `UNAVAILABLE` при `len(plans) > max`, то есть DEM-доказательство для gap молча отбрасывается. В `endpoints.py:516` при этом используется корректная обрезка `[: maximum_profiles_per_gap]` — поведение несогласовано.

**Исправление:** `>=` вместо `>`.

**Тест:** ровно `maximum_profiles_per_gap + 1` кандидатов → в DEM уходит не больше лимита.

### R8. low — `safety._has_position` принимает NaN/inf, а модуль не подключён

**Файл:** `src/warpbuster/reconstruction/safety.py:524–525`, `:434–445`

```python
def _has_position(record: ActivityRecord) -> bool:
    return record.latitude is not None and record.longitude is not None
```

Остальные пути используют `gaps.has_position`, который дополнительно проверяет `isfinite` и диапазоны. Здесь non-finite координата считается POSITIONED и может стать «стабильным» анкером в `_anchor_stability`/`_outer_bridge`.

Плюс `:434–445` помечает компонент `PROVEN_CORRUPTED/HIGH` по факту покрытия любым `corrupted_intervals`, без повторной проверки scope proof (`gaps._has_scope_proof`), в отличие от mask-логики.

**Но:** `assess_interval_safety` **не вызывается из продакшн-кода** — только из `tests/test_reconstruction_safety.py` и `tests/test_composite_gnss_regions.py` (проверено grep-ом). Сегодня это мёртвый модуль, поэтому low, а не medium. Если его подключат, оба расхождения станут реальными.

**Исправление:** переиспользовать `gaps.has_position`; подключить `_has_scope_proof` либо явно задокументировать, что модуль диагностический и не участвует в решениях.

### R9. low — hardcoded MEDIUM и глобальный счётчик очков

**Файлы:** `endpoints.py:550`, `:572`, `candidate_ranking.py:107–119`

- `choose_dem_endpoint_routes` жёстко передаёт `IntegrityConfidence.MEDIUM` и в preflight-selection, и в финальный статус, независимо от порога вызывающего. При `--min-confidence low` LOW-кандидаты не попадут в выбор, а статус будет посчитан по MEDIUM.
- В `rank_candidates` `total_points` — глобальный аккумулятор: как только он превысил `maximum_candidate_points`, **все** последующие gap-ы получают `RESOURCE_LIMIT` даже при нулевом числе точек. Поведение зависит от порядка обхода.

**Исправление:** прокинуть порог явно; считать лимит per-gap (или, если он общий, проверять `point_count > 0` и помечать `RESOURCE_LIMIT` только виновный gap).

### Общая причина R7–R9

`dem_choice.py:65` и `automatic_osm.py:232` дублируют проверку лимита DEM-профилей, а `safety.py`/`endpoints.py` держат собственные копии проверок позиции и анкеров вместо общих хелперов `gaps.py`. Это и есть источник расхождений R7–R9: стоит свести проверки в один модуль, чтобы лимиты и предикаты нельзя было разойтись.

---

## 8. Соответствие AGENTS.md

| Правило | Статус | Комментарий |
|---|---|---|
| §1 только текущий milestone | ✅ | Код соответствует ADR/задачам, забеганий вперёд не видно |
| §2 не менять правдоподобное движение | ✅ | `selection.py`/`detector.py` консервативны, UNKNOWN/MEDIUM/LOW предпочитаются |
| §3 detection ≠ reconstruction | ✅ | `integrity/` не импортирует course/OSM/DEM (проверено grep-ом) |
| §4 консервативность | ✅ | `minimum_confidence=HIGH` по умолчанию, auto-repair при низкой уверенности не выполняется |
| §5 timestamps неизменяемы | ✅ | `RepairPlan.timestamps_unchanged`, writer не трогает timestamp-поля |
| §6 FIT-first/lossless | ✅ | `fit/writer.py` патчит байты, сохраняет неизвестные поля; есть `diff`/`validate` |
| §7 минимальные изменения | ✅ | `coordinate_mask`, diff показывает только затронутые поля |
| §8 без AI | ✅ | Нет ML/LLM, рандома и сети в ядре |
| §9 без magic numbers | ⚠️ | Пороги именованы и в `IntegrityConfig`, но валидация NaN/Inf неполная (P2-1) |
| §10 vendor-neutral | ✅ | Garmin-специфика только в FIT-адаптере |
| §11 производительность | ✅ core / ❌ OSM / ⚠️ recon | 20k records ≈ 2.6 s на текущей фикстуре; O1/O2 ломают бюджет на реальных графах, R1 даёт до 126 s при 20k-точечном course и длинных контекстах |
| §12 тесты обязательны | ⚠️ | Тесты есть и зелёные, но нет CI и корневой `pytest` их не собирает (P2-5) |
| §13 приватные FIT | ✅ | `tests/private/` в `.gitignore`, `.env` игнорируется |
| §14 не угадывать FIT semantics | ✅ | Неизвестные поля сохраняются, writer консервативен |
| §15/§16 процесс | ✅ | В `tasks/` есть implemented/not implemented/tests/limits |

### Отдельно проверенная безопасность web

- `.env` не в git и не попадает в образ (`.dockerignore` + избирательный `COPY` в `Dockerfile`);
- `/download` требует cookie владельца; публичный `/api/results/{uid}` отдаёт только allowlisted-проекцию;
- GPX-парсер отвергает `<!DOCTYPE`/`<!ENTITY`;
- embedded JSON в HTML-отчёте экранирует `<`, `>`, `&`, U+2028/2029;
- CSRF/security headers присутствуют.

Замечаний уровня «уязвимость» не найдено.

---

## 9. Пробелы в тестах

Приоритетные:

1. IPv6-origin для web (`public_origin="http://[::1]:8000"` → `/health` 200).
2. NaN/±Inf для каждого float-порога `IntegrityConfig`.
3. `worker.process` при `store.get(uid) is None`.
4. `EventLog.write` с несериализуемым/NaN detail — не должно пробрасываться.
5. Парная публикация `analyze-gps --json/--gpx`: сбой второго файла не должен оставлять первый «полуопубликованным» (или поведение фиксируется тестом).
6. Регрессия на маскирование ошибок OSM-стадии (ожидаемый тип → `unavailable`; неожиданный → наружу).
7. Performance-кейс ways+refs для O1 с жёстким бюджетом.
8. Повторный `inspect()` на графе с большим числом тайлов не должен хешировать их снова (O2).
9. «stale-манифест + refresh» для O3.
10. Восстановление после stale lock для `DemCache`/`GraphCache` (O4).
11. Malformed/non-object `manifest.json` для `CacheStore.read_manifest` (O6).
12. 20k-точечный плоский course + 20k записей с бюджетом на `_CourseIndex.matches` (R1).
13. Бюджет вызовов `position`/`geodesic_distance_m` в `_options` (R2).
14. Переопределения `maximum_gap_records`/`anchor_stability_min_normal_transitions` реально влияют на `_stable_anchor` (R3).
15. READY/PARTIAL семантика: MEDIUM-кандидаты не дают `READY` либо это явно закреплено в ADR-018 (R4).
16. gap с совпадающими chainage и недоступными distance/speed не получает HIGH (R5).
17. prefix/suffix + distance spike → `preserve_recorded_distance is True` (R6).
18. `maximum_profiles_per_gap + 1` кандидатов → в DEM уходит не больше лимита (R7).

---

## 10. Проверенные и отклонённые подозрения

Чтобы не раздувать отчёт ложными находками:

- `fit/writer.py`, `definition_added += 2` — корректно (расширенное определение + переэмиссия исходного);
- compressed timestamp — соответствует модели `fitdecode 0.11` и фикстуре Garmin SDK; writer's `native_size` самосогласован;
- ветка coverage-blocked в `pipeline/repair.py` недостижима для CLI-ассерта из-за FIT-only плана;
- `unresolved_*` в `report/repair.py` не пересекаются (интервалы и gap-ы — разные домены), суммирование корректно;
- exit codes CLI совпадают с `docs/CLI_SPEC.md` (`analyze` возвращает 1 только для CORRUPTED/SUSPICIOUS; UNKNOWN не аномалия);
- `report/html.py` безопасен для embedded JSON;
- `gpx/reader.py` корректно отвергает DOCTYPE/ENTITY;
- админский rollback в `admin/cleanup.py` назван запутанно, но функционально верен;
- `_publish` в `graph_cache.py` с backup/restore корректен.

---

## 11. Рекомендованный порядок исправлений

1. **P1-1** — IPv6 `allowed_hosts` (маленький фикс + тест).
2. **O1, O2, R1** — производительность на реальных размерах: N+1 валидация, повторное хеширование графа, линейный поиск анкоров.
3. **R3, R4** — игнорируемый конфиг endpoint-анкеров и согласование `READY` с ADR-018 (это вопросы корректности решений, а не скорости).
4. **P2-1** — `math.isfinite` в `IntegrityConfig` (маленький фикс + параметризованный тест).
5. **O4** — takeover stale lock (иначе кэш клинит навсегда после SIGKILL).
6. **P2-2** — конкретные типы исключений в `pipeline/repair.py`.
7. **P2-3, P2-4** — защита `EventLog.write` и `worker.process` (по 3–5 строк).
8. **O3, O5, R2** — согласованность `stale`, запрет redirect до чтения тела, бюджет перебора пар анкоров.
9. **R5, R6** — минимальный span/понижение confidence при TIMESTAMPS и сохранение recorded distance для prefix/suffix.
10. **P2-5** — CI/скрипт на четыре набора тестов + ruff; затем решение по mypy (`files = ["src"]` или доведение web).
11. **P2-7, P3-4** — обновить дерево модулей и оговорку про DEM/offline.
12. Остальное (P3-1…P3-3, O6…O11, R7…R9) — по мере правок.

---

## 12. Итог

- **Ядро (`src/warpbuster`)** — инварианты продукта соблюдены, детекция консервативна, FIT-слой lossless, производительность на текущей фикстуре в цели. Дефектов детекции нет.
- **Reconstruction** — логика в целом соответствует ADR, но есть один алгоритмический риск производительности (R1: линейный поиск анкоров, до 126 s в худшем замере), игнорирование переданного конфига (R3) и расхождение `READY` с ADR-018 (R4). R1 стоит закрыть до использования course-реконструкции на длинных реальных треках.
- **Web** — функционально и по безопасности в порядке, но одна разрешённая конфигурация (IPv6) полностью выводит сервис из строя, плюс несколько мест, где ошибка обработчика/логгера может «подвесить» job.
- **OSM-пакеты** — самая проблемная часть по производительности: все 547 тестов зелёные, но реальный сценарий (средний экстракт + серия route-запросов) практически неработоспособен по времени. O1 и O2 стоит закрыть до любого прод-использования `osm-routing`.

Ревью выполнено без изменения кода.
