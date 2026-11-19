# WarpBuster — ревью машиночитаемого описания алгоритма

**Дата:** 18 ноября 2026
**Объект:**

- `docs/ALGORITHM.md`
- `docs/algorithm/flow.json`
- `docs/algorithm/implementation.json`
- `scripts/export_algorithm.py` (генератор)
- `tests/test_algorithm_documentation.py` (защита от drift)

**Статус:** актуализировано 19 ноября 2026 после проверки находок и релиза v0.3.5.
**Исправления:** [ffc38c7 — validate algorithm documentation and CLI outcomes](https://github.com/zloynemec/warpbuster/commit/ffc38c75d8a5645e91022cd6377920a718d1ca27).
**История:** исходное ревью от 18 ноября сохранено ниже; разделы 9–10 отражают последующее закрытие P3-1 и документационной части P2-3. Его severity, номера строк, метрики и предложенные исправления описывают состояние до релиза; текущие решения указаны в статусах находок и разделе 10; разделы 8–9 сохраняют предыдущие итоги. Пять исходных High не считаются пятью подтверждёнными блокирующими дефектами.
**Смежные материалы:** `CODE_REVIEW.md` (P2-2 закрыт в части диагностики; P2-7 и P3-4 — исторические смежные находки).

---

## 1. Исходное резюме (до исправлений)

Артефакт качественный. Выборочная сверка ~20 семантических утверждений `flow.json` с кодом **не выявила ни одного расхождения**, включая тонкие места: раздельные confidence policies, запрет записи `LOW`-кандидатов, FIT-only coverage gate до OSM/DEM/writer, FIT invalid sentinel вместо интерполяции, EGM96 только по явной декларации, `gpx_first`, `automatic_medium_below_threshold`.

Проблемы — не в семантике, а в **формате и проверяемости**:

1. счётчик «decision points» объединяет условия и подвыражения (42 % записей — `Compare`/`BoolOp`);
2. навигация `flow.json` неоднородна (часть переходов на уровне стадии, часть — внутри правил) и не проверяется на достижимость;
3. утверждение о детерминизме сильнее фактического: выход зависит от минорной версии CPython;
4. `flow.json` не покрыт ни одним digest и не валидируется схемой;
5. один генерируемый файл на 247 150 строк плохо ревьюится и конфликтует при merge.

Дополнительно: в `flow.json` не описаны два реальных отказа (`base_plan_timeout_seconds`, exit code 3), а один reason-code переиспользуется для трёх разных причин, из-за чего отчёт теряет различимость.

| Область | high | medium | low | Всего |
|---|---:|---:|---:|---:|
| Формат и навигация описания | 5 | — | — | 5 |
| Точность `flow.json` относительно кода | — | 4 | — | 4 |
| Генератор и тест | — | 2 | — | 2 |
| Код, всплывший при проверке | — | — | 3 | 3 |
| **Итого** | **5** | **6** | **3** | **14** |

### Индекс находок

| ID | Исходная severity | Область | Суть | Статус на 2026-11-19 |
|---|---|---|---|---|
| P1-1 | high | docs | «9265 decision points» — это условия + подвыражения, не решения | Отклонён как обязательное исправление |
| P1-2 | high | flow | Неоднородные `next`; три стадии недостижимы по графу | Исправлен |
| P1-3 | high | generator | Детерминизм заявлен без привязки к версии CPython | Исправлен альтернативным способом |
| P1-4 | high | flow | `flow.json` не покрыт digest и не валидируется схемой | Частично исправлен; предложение о digest отклонено |
| P1-5 | high | format | 247 150 строк / 7.6 МБ в одном генерируемом файле | Отклонён как обязательное исправление |
| P2-1 | medium | flow | Не описан `base_plan_timeout_seconds` → `PipelineError("timeout")` | Исправлен |
| P2-2 | medium | flow | Не описаны exit codes CLI (coverage refusal = 3) | Исправлен с уточнением исходной находки |
| P2-3 | medium | flow+code | `SEARCH_LIMIT_REACHED` объединяет разные ограничения | Документационная часть исправлена; новые reason codes — отдельная задача |
| P2-4 | medium | flow | Не описана проверка `duplicate gap candidates → ValueError` | Исправлен |
| P2-5 | medium | generator | Падение на отсутствующем `web/dist/assets`, протечки файлов в каталог | Исправлен в подтверждённой части |
| P2-6 | medium | test | Проверки `flow` неполные; `read_text()` без encoding | Исправлен в согласованном объёме |
| P3-1 | low | core | Широкий `except Exception` вокруг OSM (повтор открытого P2-2) | Исправлена диагностика; fallback сохранён |
| P3-2 | low | core | Дублирование расчёта статуса при merge endpoints | Отклонён: заявленная поломка не подтверждена |
| P3-3 | low | generator | `line` для `match_case`/`comprehension` — не начало конструкции | Отложен как необязательное уточнение |

---

## 2. Методика

Проверка велась тремя способами:

1. **Воспроизведение snapshot** — `--check` и тесты.
2. **Сверка утверждений с кодом** — по каждому спорному утверждению открывался указанный в `implementation` символ и читалась реальная ветвь.
3. **Аудит приватности** — поиск секретов и абсолютных путей в 17 ресурсах.

### Команды и результаты

```sh
.venv/bin/python -V                                   # Python 3.14.7
.venv/bin/python scripts/export_algorithm.py --check  # Checked docs/algorithm/implementation.json (2.4 s)
.venv/bin/python -m pytest -q tests/test_algorithm_documentation.py
# ... 3 passed in 2.60s
```

### Проверенные утверждения

| Утверждение (`flow.json` / `ALGORITHM.md`) | Где проверено | Результат |
|---|---|---|
| Relative threshold = `max(floor, median*mult, median+MAD*madmult)` при `minimum_baseline_samples` | `integrity/detector.py::_baseline_stats` | совпадает |
| Coverage = сумма active deltas только у consecutive preserved anchor-eligible fixes одной continuity с NORMAL | `pipeline/coverage.py::measure_observed_gps_coverage` | совпадает |
| `LOW` candidate не применяется даже при `minimum_confidence=LOW` | `reconstruction/selection.py:38-42` | совпадает |
| `LOW` invalidation запрещён | `pipeline/config.py::RepairPolicy.__post_init__` | совпадает |
| Unresolved INVALIDATED → FIT sentinel `0x7FFFFFFF`, не интерполяция | `fit/writer.py:120-127` | совпадает |
| `osm_graph_id` работает до проверки `osm_mode` | `pipeline/osm.py:360` | совпадает |
| Отрицательный приоритет `OUTSIDE_COVERAGE → NO_SNAP → AMBIGUOUS_SNAP` | `osm_routing/route_service.py:40` | совпадает |
| Altitude completion требует exact EGM96, Web декларацию не делает | `reconstruction/altitude_completion.py:154`, `web/.../config.py` | совпадает |
| `resultMessage`: coverage refusal / repaired partial / unchanged / OSM unavailable / unresolved | `web/dist/assets/fit-only.mjs::resultMessage` | совпадает |
| Pace = `elapsed/distance*1000`; HR time-weighted; cadence sample average с последней точкой | `report/html.py::_kilometre_splits` и др. | совпадает |
| Running cadence = `2*(cadence+fractional_cadence)` | `report/html.py::_display_cadence` | совпадает |
| Public result `schema_version=5` через allowlist | `web/.../processing.py:137-158` | совпадает |
| Private audit ограничен `_PRIVATE_FAILURE_TEXT_LIMIT` | `pipeline/osm.py:37,210-215` | совпадает |
| `gpx_first` до OSM-попыток | `reconstruction/automatic_osm.py:195-207` | совпадает |
| Approximate selection возможен только для fully ORIGINAL_MISSING с двумя анкорами | `reconstruction/approximate_contract.py::assess_gap_scope` | совпадает |
| Unreachable tail: excess = `distance - speed_limit*elapsed - budget` | `integrity/tail.py::reachability_excess` | совпадает |

### Приватность артефакта

Скан 17 ресурсов по `password|secret|token|api_key|/Users/|PRIVATE KEY|Authorization:` — **0 совпадений**, абсолютных путей `/Users/` — **0**. Заявление `ALGORITHM.md:42-45` подтверждается.

---

## 3. High

### P1-1. «9265 точек принятия решений» — это не точки принятия решений

**Статус на 2026-11-19:** Отклонён как обязательное исправление.

**Решение:** Набор `decision_points` изначально документирован как индекс условий, сравнений, short-circuit и control-flow узлов, а не число независимых решений. Счётчик не меняли; более точная терминология остаётся необязательным уточнением. Оснований для High нет.

**Файл:** `scripts/export_algorithm.py:30-48, 70-98`; `docs/ALGORITHM.md:26-28`

`decision_points` индексирует не только условия, но и их подвыражения. Фактическое распределение по `implementation.json`:

| kind | count | комментарий |
|---|---:|---|
| `Compare` | 2855 | подвыражение условия |
| `If` | 1918 | настоящее ветвление |
| `BoolOp` | 1042 | short-circuit внутри условия |
| `Raise` | 826 | явный отказ |
| `comprehension` | 807 | цикл внутри выражения |
| `IfExp` | 683 | тернарник |
| `For` | 367 | цикл |
| `Try` | 224 | обработка исключений |
| `ExceptHandler` | 224 | обработчик |
| `Continue` | 169 | управление циклом |
| `Assert` | 66 | проверка |
| `Break` | 43 | управление циклом |
| `While` | 41 | цикл |
| **Итого** | **9265** | |

`if a and b > 0` даёт три записи (`If`, `BoolOp`, `Compare`). То есть 3897 записей (42 %) — не решения. Кроме того, в индексе нет связи «какой `If` является контейнером для данного `Compare`»: только `line` и ordinal `ast.walk`.

Формулировка `ALGORITHM.md:26-28` корректна («индексирует условия, циклы, … comparisons»), но число из `counts` в коммуникации читается как «9265 решений».

**Исправление (одно из):**

- добавить в запись `container_node` — ordinal ближайшего `If`/`While`/`comprehension`/`Try`;
- переименовать счётчик в `condition_nodes` и в тексте писать «9265 узлов условий и ветвлений».

**Проверка:** `python - <<'PY'` с подсчётом `Counter(p["kind"] for m in modules for p in m["decision_points"])`.

---

### P1-2. Навигация `flow.json` неоднородна, часть стадий недостижима

**Статус на 2026-11-19:** Исправлен.

**Решение:** `ffc38c7`: `flow.json` переведён на schema 2, `entrypoints[].start` и единый `transitions[]` с `from/to/kind/when`. Подстадии помечены `kind=substage`. Валидатор проверяет ссылки, типы, дубли и достижимость всех 18 стадий по 20 связям. В старом файле переход был спрятан именно в строке `then` (`next=...`), а не в отдельном JSON-поле правила.

**Файл:** `docs/algorithm/flow.json:42-133`; `docs/ALGORITHM.md:16-18`

Переходы заданы двумя разными способами: `stage.next` и `{"next": ...}` внутри `ordered_rules`. Следствия:

- `read_inputs` не имеет `next` на уровне стадии (переход спрятан в последнем правиле);
- `detect_integrity`, `coordinate_free_course` не имеют входящих стадийных рёбер;
- `manager_and_routing` не имеет ни входящих, ни исходящих рёбер — это подстадия `osm_stage`, но по графу это изолированный узел.

Тест `tests/test_algorithm_documentation.py:26` проверяет только, что значение `next` существует среди `stage_ids`. Связность и висячие стадии не проверяются. `ALGORITHM.md:16-18` обещает навигацию «`pipeline` по `id`/`next`», которую по такой структуре нельзя пройти программно.

**Исправление:** единый массив переходов:

```json
"transitions": [
  {"from": "read_inputs", "to": "detect_integrity", "when": "inputs accepted"},
  ...
]
```

либо проставить `next` всем линейным стадиям и явно помечать подстадии (`"parent": "osm_stage"`). Добавить тест: каждая стадия достижима из какого-либо `entrypoints[].next`; нет стадий без входящих рёбер, кроме первой.

---

### P1-3. Детерминизм заявлен без привязки к версии CPython

**Статус на 2026-11-19:** Исправлен альтернативным способом.

**Решение:** `ffc38c7`: каталог содержит `generator.python_implementation` и `generator.python_version`; руководство ограничивает обещание воспроизводимости фиксированной средой и поясняет смену AST/unparse. Жёсткий guard CPython 3.14 не добавлен: поддерживаемые версии Core не ограничиваются ради экспортера. Реальное сравнение разных установленных версий CPython не выполнялось; тест проверяет фиксацию смены версии в metadata.

**Файл:** `docs/ALGORITHM.md:64-66`; `scripts/export_algorithm.py:51-67, 214-223`

Текст: «Выход детерминирован и не зависит от текущего времени, Git commit, доступности optional native dependencies и сети». Это верно для одного и того же интерпретатора, но:

- `ast.unparse` и состав полей AST меняются между минорными версиями Python (PEP 701 в 3.12, `TryStar` в 3.11 и т. п.);
- `requires-python = ">=3.14"`, то есть 3.15 не запрещён;
- `--check` сравнивает **текст** файла, поэтому любое изменение форматирования `unparse` даёт «stale» без изменения семантики.

**Исправление:** guard в `main()` и явная формулировка в документе:

```python
if sys.version_info[:2] != (3, 14):
    parser.exit(2, "algorithm export requires CPython 3.14.x\n")
```

В `ALGORITHM.md` заменить на «детерминирован для фиксированной минорной версии CPython (3.14)».

---

### P1-4. `flow.json` не покрыт digest и не валидируется схемой

**Статус на 2026-11-19:** Частично исправлен; предложение о digest отклонено.

**Решение:** `ffc38c7`: `validate_flow()` проверяет обязательную структуру, типы правил/ссылок, ID, переходы и достижимость и вызывается как при экспорте, так и при `--check`. Использована стандартная библиотека, без новой зависимости JSON Schema. Хеш `flow.json` в Markdown не добавлен: он не подтверждает соответствие ручного текста алгоритму. Семантические пояснения по-прежнему требуют ревью.

**Файл:** `docs/algorithm/implementation.json` (`source_digest_sha256`), `tests/test_algorithm_documentation.py:13-42`

`source_digest_sha256` считается по хешам модулей и ресурсов и не включает `flow.json`/`ALGORITHM.md`. Тест проверяет, что `implementation`-ссылки существуют как символы, но смысловой текст правил (`"rule"`, `"when"`, `"then"`, `"caveat"`) можно переписать, и все проверки останутся зелёными. `ALGORITHM.md:68-69` честно предупреждает об этом, но структурной защиты нет.

**Исправление:**

1. JSON Schema для `flow.json`: обязательные поля, типы, `implementation: array[string]`, `pattern ^[^:]+\.py::[A-Za-z_.]+$`, непустые `ordered_rules`, уникальность `id`.
2. Хранить `flow_sha256` в `ALGORITHM.md` и проверять его тестом. Это не доказательство истинности, но сигнал «правило изменилось — перечитай» при ревью.

---

### P1-5. 247 150 строк / 7.6 МБ в одном генерируемом файле

**Статус на 2026-11-19:** Отклонён как обязательное исправление.

**Решение:** Большой генерируемый артефакт сохранён целиком; разбиение и отключение diff не потребовались. Контрольное изменение одного config default в памяти дало 6 добавленных и 6 удалённых строк, а не тысячи. Это не исключает крупных diff при переносе строк исходника, но не подтверждает универсальность исходного утверждения. `linguist-generated` остаётся необязательной настройкой.

**Файл:** `docs/algorithm/implementation.json`

Каждое изменение номеров строк, общего конфига или шаблона переписывает крупные регионы файла. Практические следствия:

- `git blame` и построчное ревью неработоспособны;
- merge-конфликты при параллельных изменениях в разных подсистемах;
- PR с одной правкой в `config.py` выглядит как правка на тысячи строк.

Для артефакта, чья ценность — аудируемость, это существенный минус. Полнота и один файл — не одно и то же.

**Варианты (по возрастанию сложности):**

1. оставить один файл, добавить `.gitattributes`:
   ```
   docs/algorithm/implementation.json linguist-generated=true -diff
   ```
   и написать в `ALGORITHM.md`, что файл проверяется через `--check`, а не построчным diff;
2. разбить на `docs/algorithm/modules/<path>.json` + `manifest.json` (digest остаётся тем же);
3. хранить только `program` без `line` для неизменённых узлов — не рекомендуется, ломает трассировку к исходнику.

---

## 4. Medium

### P2-1. Не описан отказ по бюджету базового плана

**Статус на 2026-11-19:** Исправлен.

**Решение:** `ffc38c7`: в `base_plan_status.completion_checks` и `scenario_matrix` описаны `base_plan_timeout_seconds` (default 180 s), `PipelineError timeout`, отсутствие FIT output и CLI exit 2. Уточнено: elapsed считается от начала `run_repair`, проверка выполняется после базового планирования и не является прерывающим deadline внутри planner.

**Файл:** `src/warpbuster/pipeline/repair.py:133-134`; `docs/algorithm/flow.json`

```python
if time.monotonic() - started > execution.base_plan_timeout_seconds:
    raise PipelineError("timeout", "base repair planning exceeded its time budget")
```

`base_plan_timeout_seconds` (180 с по умолчанию) и соответствующий `PipelineError("timeout")` отсутствуют в `flow.json`: ни в `read_inputs`, ни в `base_gpx_plan`, ни в `scenario_matrix`. Бюджеты OSM/DEM описаны, этот — нет, хотя это отдельный исход запуска.

**Исправление:** добавить правило в `base_gpx_plan` и строку в `scenario_matrix` («бюджет базового плана исчерпан → timeout, FIT не пишется»).

---

### P2-2. Не описаны exit codes CLI

**Статус на 2026-11-19:** Исправлен с уточнением исходной находки.

**Решение:** `ffc38c7`: `exit_codes` добавлены в `cli_process` и `cli_legacy_repair`, включая dry-run, отказ записи и ошибку HTML после уже созданного FIT. Формула «coverage refusal всегда = 3» отклонена: ветвь `plan.status == NOT_NEEDED` проверяется раньше и возвращает 0 даже при BELOW_THRESHOLD. Это воспроизведено на синтетическом FIT с timestamps 0, 3600, 3601; данные пользователя не использовались.

**Файл:** `src/warpbuster/cli.py:590-594, 651-660, 720-760`; `docs/algorithm/flow.json:34`

`cli_process` описывает поведение, но не коды выхода. `cli_analyze` коды перечисляет, поэтому читатель ждёт того же и здесь. Фактически:

- `0` — успех;
- `1` — `CORRUPTED`/`SUSPICIOUS` в `analyze`;
- `2` — аргументы/чтение/отчёт;
- `3` — coverage refusal и `repair_refused` (в т. ч. `FitWriteError`).

**Исправление:** добавить `exit_codes` в оба entrypoint-описания или строку в `scenario_matrix`.

---

### P2-3. `SEARCH_LIMIT_REACHED` переиспользуется для трёх разных причин

**Статус на 2026-11-19:** Исправлен в согласованном документационном объёме; новые reason codes — отдельная задача по диагностике.

**Решение:** `ffc38c7`: в `base_gpx_plan.limit_reasons` явно перечислены условия, возвращающие общий `search_limit_reached`. Новые `GAP_RECORD_LIMIT`/`SEARCH_WINDOW_LIMIT` в Core не вводились. Последующее уточнение: в `flow.json` и `ALGORITHM.md` перечислены оба источника `_LimitReached`, точные операторы сравнения, порядок проверок и применение того же reason в `apply_confirmed_osm_routes`. Для automatic OSM `automatic_osm_json.decisions[].reason = gap_record_limit` при общем reconstruction reason. Лимиты контекстного окна сами по себе не означают `SEARCH_LIMIT_REACHED`. Изменение диагностического контракта вынесено в отдельную задачу; исходное предложение ввести новые коды ниже не является принятым изменением.

**Файл:** `src/warpbuster/reconstruction/local.py:242-257, 728`; `src/warpbuster/reconstruction/automatic_osm.py:211-213`; `docs/algorithm/flow.json:75`

| Условие | Reason | Строка |
|---|---|---|
| `number >= maximum_reconstruction_intervals` | `SEARCH_LIMIT_REACHED` | `local.py:243` |
| `gap.record_count > missing_completion_max_run_records` | `SEARCH_LIMIT_REACHED` | `local.py:253` |
| `_LimitReached` при переборе окон | `SEARCH_LIMIT_REACHED` | `local.py:728` |
| `gap.record_count > cfg.maximum_gap_records` (OSM) | `SEARCH_LIMIT_REACHED` | `automatic_osm.py:213` |

В отчёте невозможно отличить «слишком много gap'ов» от «gap слишком длинный» от «исчерпан перебор окон»: пользователь видит один и тот же reason, хотя действия разные.

`flow.json:75` формулирует это как «Record budget превышен … → unresolved с соответствующим reason», создавая впечатление отдельного кода.

**Исправление:** ввести `GAP_RECORD_LIMIT` (и, при желании, `SEARCH_WINDOW_LIMIT`), обновить `flow.json` и тесты отчёта.

---

### P2-4. Не описана проверка `duplicate gap candidates`

**Статус на 2026-11-19:** Исправлен.

**Решение:** `ffc38c7`: в `automatic_osm_selection.ordered_rules` и отдельном сценарии описан `ValueError duplicate gap candidates`, отличающийся от per-gap duplicate route IDs. Уточнено сохранение base plan внешней границей optional-stage ошибок.

**Файл:** `src/warpbuster/reconstruction/automatic_osm.py:146`; `docs/algorithm/flow.json:109-116`

```python
if len(candidates) != len(plan.interval_plans):
    raise ValueError("duplicate gap candidates")
```

`flow.json:110` перечисляет сверку activity/mask/gap snapshot и discovery inventory, но не эту проверку. При этом `duplicate route IDs` (строка 112) — другая проверка (per-gap block reason), их легко спутать.

**Исправление:** добавить в правило 110 «повторяющиеся gap candidates» либо в `not_claimed`/`semantics` отдельной строкой.

---

### P2-5. Генератор падает на отсутствующем `web/dist/assets` и пускает в каталог лишние файлы

**Статус на 2026-11-19:** Исправлен в подтверждённой части.

**Решение:** `ffc38c7`: восемь JS-файлов перечислены в `RESOURCE_PATHS`; посторонний `debug.js` не включается. Отсутствие перечисленного ресурса даёт сообщение `missing resource: <path>`; CLI экспортера завершает ожидаемые ошибки с кодом 2 без traceback. Оба случая покрыты тестами. Утверждение об отсутствии `web/dist/assets` в обычном чистом checkout отклонено: нужные assets отслеживаются Git.

**Файл:** `scripts/export_algorithm.py:163-167`

```python
assets = sorted(
    p for p in (root / "web/dist/assets").iterdir()
    if p.is_file() and p.suffix in {".mjs", ".js"}
)
```

- если каталога нет (чистый checkout без `web/dist`, очищенный build) — голый `FileNotFoundError` со стеком вместо понятного сообщения;
- любой посторонний файл в `web/dist/assets` молча попадает в `scope.resources` и меняет snapshot;
- список ресурсов задан двумя способами: `RESOURCE_PATHS` (явный) и glob (неявный).

**Исправление:** явный манифест JS-файлов в `RESOURCE_PATHS` (или проверка «неизвестный файл → ошибка»), проверка существования каждого пути с сообщением вида `missing resource: <path>`.

---

### P2-6. Проверки `flow.json` неполные, чтение без encoding

**Статус на 2026-11-19:** Исправлен в согласованном объёме.

**Решение:** `ffc38c7`: JSON читается в UTF-8; общий валидатор отклоняет неправильные типы, неизвестные ссылки, дубли, неверные переходы и недостижимые подстадии. Добавлены негативные тесты, в том числе прежний пропуск строки вместо массива `implementation`. Смысловая полнота `scenario_matrix` не объявляется автоматически доказанной; новая зависимость для схемы не добавлялась.

**Файл:** `tests/test_algorithm_documentation.py:10, 15, 20-35`

1. `check()` обрабатывает `implementation` только если это `list`: строка `"implementation": "src/....py::x"` пройдёт молча.
2. `elif key == "next": assert item in stage_ids` — если `next` станет списком, получим `TypeError: unhashable type: 'list'` вместо осмысленного assert.
3. `set(item) <= symbol_ids` скрывает дубли в одном `implementation`-массиве.
4. `read_text()` без `encoding="utf-8"` (строки 10 и 15) — зависит от локали; на Windows/non-UTF8 окружении чтение упадёт. Генератор пишет явно UTF-8.
5. Нет проверки, что `scenario_matrix` покрывает все `RepairPlanStatus`/`CoverageStatus`, и что `entrypoints` не ссылаются на несуществующие стадии (частично покрыто рекурсией).

**Исправление:** добавить схему (см. P1-4), `encoding="utf-8"`, явные ветки для строки/списка в `next`, проверку дублей и достижимости.

---

## 5. Low

### P3-1. Широкий `except Exception` вокруг OSM-стадии

**Статус на 2026-11-19:** Исправлена диагностика; fallback сохранён.

**Решение:** В последующей задаче 2026-11-19 добавлен `osm_failure()`: bounded type/reason/traceback с chained exceptions, без frame locals. Диагностика сохраняется в существующий private OSM audit (Web mode 0600, без HTTP-выдачи). Broad catches и безопасный base GPX plan намеренно сохранены; сломанный formatter не отменяет fallback. Общий formatter перенесён в Core, Web imports совместимы. Смежная находка `CODE_REVIEW.md` P2-2 также отмечена как исправленная в части диагностики. Ограничение: hard kill/segfault/отсутствующий IPC не гарантируют Python traceback.

**Файл:** `src/warpbuster/pipeline/repair.py:187-189`

Уже зафиксировано в `CODE_REVIEW.md` как **P2-2**; на 18.11.2026 остаётся открытым. Подтверждаю при проверке: `except Exception` превращает любой `TypeError`/`KeyError` в `routing_failed` без traceback и private audit, что противоречит `flow.json:105` («safe stage/error_code») и скрывает дефекты Core.

**Исправление:** сузить до `(OSMReconstructionError, OSError, TimeoutError, ImportError)`, неожиданное — логировать и пробрасывать.

---

### P3-2. Дублирование расчёта статуса при merge endpoints

**Статус на 2026-11-19:** Отклонён: заявленная поломка не подтверждена.

**Решение:** `RepairSelection.has_changes` уже включает `distance_spike_repairs`, поэтому утверждение об их игнорировании неверно. Эквивалентная копия плана делает `plan is not osm.plan` истинным и входит в ветку объединения audit, а не пропускает её. Конкретный пример потери audit/неверного статуса не подтверждён; общий helper и новый флаг не добавлялись.

**Файл:** `src/warpbuster/pipeline/repair.py:218-263`

После слияния endpoint-кандидатов статус плана пересчитывается вручную:

```python
status=RepairPlanStatus.PARTIAL if endpoint_selection.has_changes and plan.unresolved_gaps
else RepairPlanStatus.READY if endpoint_selection.has_changes
else RepairPlanStatus.REFUSED,
```

Это копия логики `local.py:274-284`, но без учёта `integrity.distance_spike_evidence`. Плюс вся ветка опирается на identity-сравнение `plan is not osm.plan` (строки 220, 256), которое нигде не задокументировано: если `run_dem_stage` начнёт возвращать эквивалентную копию плана, private audit перестанет мержиться, а статус разойдётся с `build_repair_plan`.

**Исправление:** вынести `_plan_status(plan, selection)` в `models/reconstruction.py` и использовать в обоих местах; заменить identity-проверки на явный флаг «план изменён».

---

### P3-3. `line` для `match_case`/`comprehension` — не начало конструкции

**Статус на 2026-11-19:** Отложен как необязательное уточнение.

**Решение:** Алгоритм выбора `line` не меняли. Для AST-узлов без собственной позиции это привязка к строке потомка; руководство не обещает строку ключевого слова `case`/`for`. Возможное уточнение терминологии оставлено отдельно. Замена на `node.iter.lineno` не является универсальным улучшением: в многострочном comprehension target может начинаться раньше iter.

**Файл:** `scripts/export_algorithm.py:75-78`

```python
line = line or min(descendants, default=1)
```

Для узлов без `lineno` берётся минимальная строка **потомка**, то есть для многострочного `case`/comprehension это может быть строка внутри тела. Сейчас `Match` в коде нет, а у 807 comprehensions строка почти всегда совпадает, но поле документировано как точная ссылка на исходник (`ALGORITHM.md:28`) и начнёт врать при появлении многострочных `match`.

**Исправление:** для `match_case` брать `node.pattern` (у pattern есть `lineno`), для `comprehension` — `node.iter.lineno`; либо явно документировать приблизительность.

---

## 6. Что сделано хорошо

- **Семантика `flow.json` точна** в проверенных местах, включая редкие ветви: `automatic_medium_below_threshold`, `gpx_first`, coverage refusal до OSM/DEM, sentinel вместо интерполяции, `no_fit_altitude_stream`.
- **`scenario_matrix`** — сильная часть: покрывает не только happy path, но и «не-доказательства» (course deviation, distance/speed mismatch, HIGH application threshold, dry-run, overwrite).
- **Границы полноты** (`ALGORITHM.md:71-86`) сформулированы честно: не обещают исчерпывающего call graph, достижимости ветвей и детерминизма внешней среды.
- **Генератор безопасен**: только stdlib, без импорта приложения, без сети и Git; все выражения идут через `unparse`, поэтому `bytes`/`inf`/f-strings не ломают JSON.
- **Приватность подтверждена**: 0 секретов и абсолютных путей в ресурсах; `tests/private/` не попадает в scope.
- **Отдельные `sha256` по файлам + самоописывающий `encoding`-блок** — правильная основа для снимка; восстановление состояния не требует Git.

---

## 7. Исходный рекомендуемый порядок (история)

Этот список сохранён как предложение автора первоначального ревью. Текущее решение по каждому пункту — в статусах выше и итогах ниже; отклонённые предложения не являются планом работ.

1. **P1-1, P1-2, P1-3** — дешёвые правки, убирают неверные ожидания от формата и будущие ложные «stale».
2. **P2-1, P2-2, P2-4** — дописать `flow.json` (бюджет базового плана, exit codes, duplicate gap candidates).
3. **P2-3** — разделить reason-code в коде и синхронизировать `flow.json`; это улучшает и отчёты, и документ.
4. **P2-5, P2-6** — устойчивость генератора и полнота теста.
5. **P1-4, P1-5** — решение по формату (схема + разбиение/`.gitattributes`) принять до того, как артефакт начнут использовать как контракт.
6. **P3-1, P3-2** — отдельной задачей по надёжности pipeline; P3-1 уже есть в `CODE_REVIEW.md`.
7. **P3-3** — по мере появления `match` в коде.

---

## 8. Итоги релиза ffc38c7 — 2026-11-19 (история)

Исправления четырёх согласованных задач вошли в `ffc38c7`, отправлены в `main`
и развёрнуты как v0.3.5. GNSS detector, reconstruction и FIT writer не менялись.
Обновление этого ревью только фиксирует решения; новых исправлений алгоритма нет.

| Итог | Количество | Находки |
|---|---:|---|
| Исправлены в согласованном объёме | 7 | P1-2, P1-3, P2-1, P2-2, P2-4, P2-5, P2-6 |
| Частично исправлены; остаток отклонён или вынесен отдельно | 2 | P1-4, P2-3 |
| Отклонены как обязательные исправления / неподтверждённые дефекты | 3 | P1-1, P1-5, P3-2 |
| Отдельная задача или необязательное уточнение | 2 | P3-1, P3-3 |
| **Всего** | **14** | |

### Реализованный объём

1. Schema 2 с `entrypoints[].start`, единым `transitions[]`, явными подстадиями
   и проверкой структуры, ссылок и достижимости.
2. Уточнены timeout базового плана, duplicate gap candidates и реальные исходы
   CLI, включая NOT_NEEDED до coverage refusal и HTML error после FIT publication.
3. Зафиксированы версия/реализация Python для экспортера; JSON читается в UTF-8.
4. Состав JS-ресурсов задан явно; посторонние файлы игнорируются, отсутствие
   обязательного ресурса диагностируется.

### Проверки релиза ffc38c7

```sh
.venv/bin/python scripts/export_algorithm.py --check
.venv/bin/python -m pytest -q tests/test_algorithm_documentation.py tests/test_fit_only_coverage.py tests/test_fit_only_cli_reports.py tests/test_pipeline_boundary.py
# 56 passed
.venv/bin/ruff check scripts/export_algorithm.py tests/test_algorithm_documentation.py
.venv/bin/ruff format --check scripts/export_algorithm.py tests/test_algorithm_documentation.py
```

Проверены 18 достижимых стадий и 20 связей. В production подтверждены образ v0.3.5,
revision `ffc38c75d8a5645e91022cd6377920a718d1ca27`, healthy container и успешные
HTTP-ответы `/`, `/fix`, `/faq`, `/health`. Это сведения о проверке релиза,
а не утверждение о бессрочном текущем состоянии сервиса.

### Оставшиеся ограничения

- Валидатор проверяет структуру и ссылки, но не доказывает истинность ручных
  пояснений или полноту всех возможных исполнений.
- Поведение AST между реально установленными разными версиями CPython не
  сравнивалось; metadata смены версии проверяются тестом.
- Разделение Core reason codes, приватная диагностика неожиданных OSM ошибок
  и уточнение позиции AST-узлов не реализованы этим релизом.

### История обновления документа

- 2026-11-18: первоначальное ревью без изменения исходников алгоритма.
- 2026-11-19: после сверки замечаний и релиза `ffc38c7` добавлены текущие статусы,
  принятые альтернативы, отклонения и результаты проверок. Исходные формулировки,
  severity и метрики сохранены как история, чтобы не скрывать причины решений.
- 2026-11-19: отдельной задачей закрыта потеря private diagnostics P3-1; итог обновлён
  в разделе 9 без перезаписи состояния прежнего релиза ffc38c7.
- 2026-11-19: закрыт согласованный документационный объём P2-3; полный перечень
  применений общего reason и граница отдельной задачи по кодам описаны в разделе 10.

---

## 9. Последующее закрытие P3-1 — 2026-11-19

По отдельному поручению добавлена приватная диагностика неожиданных ошибок OSM.
Итог после этой задачи: 8 находок исправлены в принятом объёме, 2 частично исправлены,
3 отклонены как обязательные исправления, 1 необязательное уточнение (P3-3)
остаётся отложенным. Таблица раздела 8 сохраняет состояние предыдущего релиза.

Публичные status/error codes и GPX fallback не меняются. Диагностика добавляется
на outer boundary, внутри broad coverage/routing catches и при Python exception
в child. Форматтер bounded, UTF-8 writable и не включает frame locals. Сбой
форматирования оставляет только bounded exception_type и не отменяет repair.
Существующий audit разрешён только локальному оператору, не HTTP-клиентам.

Проверки: byte-identical FIT fallback; RuntimeError/TypeError/KeyError и chained
cause; mode 0600 и отсутствие diagnostics в public JSON/HTML/status; запрет
HTTP-доступа к audit владельцу и гостю; UTF-8 surrogate path; сломанный formatter;
сериализация child diagnostic через IPC. Настоящий process-group тест требует
Linux, поскольку product OSM isolation на macOS не поддерживается.

Внешние trace/log сервисы, новые retries, изменение reconstruction policy и
пробрасывание неожиданных OSM ошибок вместо fallback не добавлены.

---

## 10. Документационная часть P2-3 — 2026-11-19

По отдельному поручению описано существующее переиспользование
`SEARCH_LIMIT_REACHED`, без изменения Core reason codes или алгоритма.
В `ALGORITHM.md` и `flow.json` явно различены:

- количество gaps в базовом плане и record limit отдельного gap;
- превышение числа deduplicated anchor matches и бюджета uncached path evaluations;
- record limit при automatic и explicitly confirmed OSM application;
- общий reconstruction reason и automatic audit marker `gap_record_limit`.

Указаны точные сравнения (`>=`/`>`), приоритет более ранних отказов и отличие
от ограничений сбора контекстного окна, которые сами по себе этот reason не задают.
По общему reason нельзя определить конкретный бюджет или отсутствие допустимого
маршрута. В этих ветвях gap остаётся unresolved; последующие этапы имеют свои gates.

Текущий итог: 9 находок исправлены в согласованном объёме, 1 частично исправлена
(P1-4), 3 отклонены, 1 необязательное уточнение (P3-3) отложено. Таблицы прежних
релизов сохранены как история. Разделение reason codes остаётся отдельной задачей
по диагностике с проверкой совместимости JSON/отчётов и callers.

Проверка: `scripts/export_algorithm.py --check`, существующие тесты документации,
local matching и automatic/confirmed OSM application. Новые тесты поведения
не добавлялись: исходники алгоритма не менялись.

---

## Приложение A. Метрики артефакта (первоначальный снимок)

| Показатель | Значение |
|---|---|
| `implementation.json` | 247 150 строк, 7.6 МБ |
| Python-модулей | 127 |
| Символов | 1 188 |
| Записей `decision_points` | 9 265 |
| Ресурсов (JS/config/template) | 17 |
| Время `--check` | ≈ 2.4 с |
| Время тестов | 2.6 с (3 passed) |
| Python | 3.14.7 |
| Секретов/абсолютных путей в ресурсах | 0 |

## Приложение B. Соответствие исходным файлам

| Проверяемая область | Где смотреть |
|---|---|
| Формат `program`/`decision_points`/`symbols` | `docs/ALGORITHM.md:14-40`, `implementation.json → encoding` |
| Порядок правил и «первое применимое» | `flow.json → semantics.rule_order` |
| Инварианты продукта | `flow.json → invariants` |
| Точка входа и политика | `flow.json → entrypoints`, `pipeline/config.py::RepairPolicy` |
| Отказы и их коды | `cli.py`, `pipeline/repair.py`, `web/.../processing.py` |
| Границы полноты | `ALGORITHM.md:71-86` |
