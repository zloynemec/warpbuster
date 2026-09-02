# Task 008A — Numbered Detector Analysis HTML Report

## Статус

Реализована 2026-09-02.

## Проблема

Текущий `warpbuster analyze --html` показывает исходный track, графики и detector
findings, но отчёт недостаточно удобен для ручной проверки detector-а на реальной
активности:

- найденные переходы, intervals, warnings и missing-position runs трудно однозначно
  сопоставить с местом на карте;
- на карте и в таблицах нет общей стабильной нумерации диагностических участков;
- `analyze` не умеет показать опциональный GPX course рядом с исходным FIT;
- GPX course сейчас виден в основном через repair report, из-за чего визуальная
  диагностика смешивается с reconstruction planning и создаёт впечатление, что FIT
  должен быть исправлен;
- длинный locally-plausible drift, который detector не распознал, визуально трудно
  отличить от участка, который detector действительно классифицировал.

Нужен analysis-only режим, позволяющий открыть один HTML, увидеть исходный FIT,
опциональный reference course, нумерованные диагностические участки и таблицу с точной
расшифровкой решения detector-а.

## Цель

Расширить существующий `analyze --html`, не создавая новый HTML-шаблон и не запуская
repair:

```bash
warpbuster analyze activity.fit --html detector-report.html
warpbuster analyze activity.fit --course race.gpx --html detector-report.html
warpbuster analyze activity.fit --course race.gpx --html detector-report.html --json
```

Отчёт должен:

1. использовать только исходный FIT как activity data;
2. показывать опциональный GPX course отдельным reference-слоем;
3. наносить на карту детерминированно пронумерованные диагностические участки;
4. использовать те же номера в таблице расшифровки;
5. объяснять, что именно увидел detector, что он не доказал и почему;
6. не создавать candidate/repaired geometry и не записывать FIT.

## Архитектурная граница

Pipeline остаётся разделённым:

```text
FIT -> normalize -> Integrity Detector -> report projection -> shared HTML renderer
                                                       \
                                                        optional GPX display layer
```

Обязательные правила:

- `analyze_integrity()` вызывается без course и не получает course-derived data;
- GPX читается только для формирования display layer после получения
  `IntegrityReport`;
- distance-to-course, совпадение с course, course direction и красота результата не
  изменяют status, confidence, reasons, corrupted intervals или repair eligibility;
- отклонение исходного FIT от GPX не создаёт диагностический участок само по себе;
- renderer не реализует новые detection rules и не повышает severity;
- timestamps, coordinates, distance и sensor fields исходного FIT неизменяемы;
- FIT writer, reconstruction planner, repair selection и FIT diff в этом режиме не
  вызываются;
- отсутствие `--course` не меняет detector result или существующий CLI exit code.

## CLI-контракт

Добавить к `warpbuster analyze` опциональный аргумент:

```text
--course COURSE   show a reference GPX course in the HTML report without using it for detection
```

Ограничения:

- новый `--course` поддерживается для FIT activity input;
- `--course` разрешён только вместе с `--html`; использование без `--html` возвращает
  exit code `2` и понятную ошибку;
- существующий `warpbuster analyze activity.gpx` продолжает работать без изменений;
- `analyze activity.gpx --course ...` не входит в этот task и должен получить явную
  ошибку, а не молча проигнорированный аргумент;
- `--json` совместим с `--html` и продолжает печатать detector JSON в stdout;
- optional course не добавляется в detector JSON scope и не меняет его schema/status;
- существующая защита HTML destination от неявной перезаписи сохраняется;
- явный `--overwrite` разрешает атомарно заменить только analysis HTML и требует
  `--html`; source FIT остаётся read-only;
- значение `--html` опционально: bare flag использует `<activity-stem>.analyze.html`,
  явный path его переопределяет, а отсутствие флага не создаёт report;
- ошибка чтения FIT, GPX course или записи HTML возвращает exit code `2`;
- `SUSPICIOUS/CORRUPTED` по-прежнему возвращает exit code `1`, даже если HTML успешно
  создан.

Новый command и отдельный `--dry-run` для этого режима не нужны: `analyze --html` уже
является read-only analysis workflow.

## Единый HTML-шаблон

Продолжать использовать только:

`src/warpbuster/report/assets/report.html`

Запрещается создавать копию шаблона для detector report.

Предпочтительный API:

```python
write_analyze_html(
    activity,
    integrity,
    output_path,
    *,
    course: CourseData | None = None,
    overwrite: bool = False,
)
```

Analyze payload должен по-прежнему иметь `report_kind="analyze"`. При наличии course
заполняется уже существующий `tracks.course`; `tracks.candidate`, `tracks.repaired`,
`repair` и `write_result` остаются `null`.

Template обязан условно показывать analysis-only sections по данным payload, а не по
отдельному файлу или fork-нутому JavaScript.

## Reporting-only модель диагностического участка

Добавить явную report projection, например `diagnostic_regions`. Это не новая detector
model и не новый вид corruption evidence.

Каждый display region содержит как минимум:

- `display_id` — целое число `1..N`;
- `kind`;
- `detector_stage` либо `data_quality` для missing runs;
- `start_record_index`, `end_record_index`;
- start/end timestamps и duration, если доступны;
- `continuity_id`;
- status/classification/state;
- confidence;
- `repair_eligible`;
- machine-readable reasons;
- ссылку на исходные detector entities/evidence;
- position и missing record counts;
- доступные metrics соответствующего типа;
- карту фактически существующей geometry, не соединяющую missing gaps.

Минимальные `kind`:

- `CORRUPTED_INTERVAL` — принятый detector interval;
- `ONE_SIDED_DIAGNOSTIC` — reconstructable или unresolved one-sided diagnostic, если
  он ещё не представлен accepted interval;
- `ABNORMAL_TRANSITION_RUN` — оставшиеся соседние `SUSPICIOUS/IMPOSSIBLE` transitions;
- `GEOMETRY_WARNING`;
- `VERTICAL_WARNING`;
- `MISSING_POSITION_RUN` — отсутствие coordinates; это data-quality region, а не
  доказанная corruption.

Composite/mixed-region данные показываются как часть соответствующего detector
diagnostic, если они уже существуют в `IntegrityReport`. Analysis renderer не должен
запускать reconstruction safety assessment только ради их построения.

### Reporting aggregation

Допускается только детерминированная presentation aggregation:

- соседние abnormal transitions объединяются в один `ABNORMAL_TRANSITION_RUN`, если
  следующий transition начинается в record, которым закончился предыдущий, и оба
  принадлежат одному continuity domain;
- classification и reasons каждого исходного transition сохраняются в evidence list;
- abnormal transitions, уже объясняющие accepted interval или retained one-sided
  diagnostic, не получают второй top-level номер, но доступны внутри его evidence;
- missing run остаётся самостоятельным data-quality region даже при overlap с другим
  diagnostic; overlap явно показывается в таблице;
- renderer не объединяет участки по близости к course, screen distance, похожему цвету,
  athlete/device identity или произвольному record gap.

## Стабильная нумерация

Номера должны совпадать во всех частях одного отчёта:

- marker/label на карте;
- legend/tooltip;
- строка summary table;
- expanded evidence details;
- ссылки map -> table и table -> map.

Порядок нумерации:

1. `continuity_id`;
2. `start_record_index`;
3. `end_record_index`;
4. фиксированный documented `kind` priority;
5. стабильный source/evidence key как последний tie-breaker.

Нумерация является presentation identity и не должна записываться обратно в detector
models. Одинаковые inputs и config дают одинаковые номера.

## Карта

Переиспользовать существующий Leaflet renderer и layer control.

Обязательные слои:

- `Original FIT` — исходная solid geometry;
- `Reference course` — только при `--course`;
- `Corrupted intervals`;
- `Suspicious / impossible transitions`;
- `One-sided diagnostics`;
- `Geometry warnings`;
- `Vertical warnings` при наличии отображаемой позиции;
- `Missing-position runs / bridges`.

Требования:

- numbered label хорошо читается на светлой и тёмной части basemap;
- каждый top-level diagnostic region имеет один основной номер;
- interval geometry подсвечивается только по имеющимся positioned records и
  разрывается на missing/continuity boundaries;
- missing run показывается существующим dashed bridge style, а не solid inferred path;
- если доступны только один или ни одного spatial anchor, таблица остаётся полной, а
  карта показывает доступный boundary marker либо явно сообщает `not mappable`;
- клик по номеру открывает краткий tooltip и выделяет соответствующую строку таблицы;
- клик по строке таблицы zoom/pan-ит карту к участку и визуально выделяет его;
- tooltip содержит номер, kind, records, time, status/confidence и краткие reasons;
- layer visibility не меняет numbering;
- `fit-to-track` учитывает исходный FIT и optional course, но не строит прямую линию
  через missing data;
- legend однозначно различает detector evidence, advisory warning, missing data и
  reference course;
- raw course deviation не подсвечивается как anomaly.

## Таблица расшифровки

После карты добавить общую таблицу `Numbered detector regions`.

Минимальные колонки:

- `#`;
- `Type / stage`;
- `Records`;
- `Time / duration`;
- `Positions / missing`;
- `Status / classification`;
- `Confidence`;
- `Repair eligible`;
- `Evidence / reasons`;
- `Metrics`;
- `Map` action.

Metrics должны быть type-specific, например:

- transition distance, elapsed time и apparent speed;
- interval bridge distance/speed и trusted anchors;
- one-sided anchor normal-context counts, component/tainted counts и refusal reasons;
- geometry chord/path/deviation metrics;
- vertical delta/rate;
- missing run count, anchor records, elapsed time, chord и recorded-distance delta.

Raw evidence можно показывать через раскрываемую detail row, но machine-readable reason
нельзя заменять только человекочитаемым пересказом.

Над таблицей добавить короткое объяснение:

- номер означает display region, а не severity;
- GPX — только reference overlay;
- `MISSING` не означает `CORRUPTED`;
- физически правдоподобное отклонение от GPX detector обязан оставить неизменным;
- участок исходного FIT без номера может визуально выглядеть неправильным, если
  detector не получил достаточного course-independent evidence.

## Optional GPX course

Course читается существующим `read_gpx_course()` и сериализуется существующим
`_course_track()`.

В summary показать:

- source path/name;
- segment count;
- point count;
- total course distance;
- заметную пометку `Reference only — excluded from integrity detection`.

Не добавлять в этом task:

- distance-to-course classification;
- map matching;
- course projections в detector reasons;
- candidate coordinates;
- direction/span selection для repair;
- попытку автоматически объявить off-course участок corrupted.

## Payload и renderer responsibilities

Python report layer отвечает за:

- детерминированное построение `diagnostic_regions` из существующих detector outputs;
- нумерацию;
- evidence links;
- type-specific metrics;
- корректное splitting по positions/continuity;
- optional course payload.

HTML/JavaScript отвечает только за:

- rendering layers;
- marker labels;
- filters/layer visibility;
- map/table navigation;
- formatting уже рассчитанных metrics.

JavaScript не должен заново классифицировать transitions, группировать detector
evidence или вычислять confidence.

## Privacy и offline behavior

Сохранить существующую модель HTML report:

- FIT coordinates, GPX course и telemetry embedded в локальный HTML;
- application CSS/JS/report payload находятся в одном output file;
- Leaflet и OSM tiles используют существующие pinned external URLs;
- report явно предупреждает, что при открытии CDN/tile providers видят IP и область
  запрошенных tiles;
- FIT/GPX не загружаются отдельным API payload;
- report открывается напрямую с диска без локального HTTP server;
- при недоступной сети таблица и embedded diagnostic data остаются читаемыми, даже если
  basemap/Leaflet assets не загрузились полностью.

## Производительность и размер

- detector выполняется ровно один раз;
- optional course parsing и serialization линейны по числу course points;
- report projection линейна по records/findings/retained diagnostics;
- запрещён полный O(activity_records * course_points) nearest-course scan;
- HTML generation для ~20 000 FIT records должна оставаться практически пригодной и
  не превышать существующую performance target без documented regression;
- большое число regions не должно приводить к молчаливой потере данных: любое
  presentation truncation имеет named limit, полный count и явный warning.

## Перед реализацией

До изменения production code:

1. перечислить точные report entities, доступные из текущего `IntegrityReport`;
2. определить `kind` priority и overlap policy;
3. показать пример numbering для synthetic activity с interval, raw suspicious run и
   missing gap;
4. подтвердить, что optional course не попадает в detector call graph;
5. перечислить затрагиваемые файлы и спорные UI/data-model решения;
6. проверить текущий HTML payload/schema и не дублировать уже существующие layers и
   missing-run helpers.

Ожидаемые production files:

- `src/warpbuster/cli.py`;
- `src/warpbuster/report/html.py`;
- `src/warpbuster/report/assets/report.html`.

Допускаются изменения report models/helpers и документации, если они необходимы для
чистого контракта. Integrity/reconstruction/writer code не должен меняться без отдельно
доказанной необходимости.

## Тесты

Добавить/обновить как минимум:

- CLI test: FIT analyze HTML без course сохраняет прежнее поведение;
- CLI test: FIT + `--course` создаёт analysis report и не создаёт FIT output;
- CLI test: `--course` без `--html` и GPX activity + `--course` получают exit code `2`;
- test, что integrity JSON/status/confidence/reasons идентичны с course и без course;
- test, что detector вызывается без course-derived input;
- payload test для каждого `diagnostic_regions.kind`;
- deterministic numbering test;
- adjacent abnormal transition aggregation test;
- overlap test: interval evidence не получает дублирующий top-level number;
- missing-overlap test: missing run остаётся отдельным data-quality region;
- continuity test: geometry не соединяется через boundary;
- no-position/not-mappable test;
- map/table ID consistency test;
- row-to-map и marker-to-table JavaScript smoke test;
- optional course layer/summary/disclaimer test;
- escaping/CSP-safe embedded JSON regression;
- existing destination/no-overwrite test;
- assertion, что используется единственный shared template;
- performance regression на synthetic ~20 000-record activity;
- полный существующий test/lint/type-check suite.

## Private acceptance: CWT Dzhurla 2025

При наличии ignored private fixtures:

```text
tests/private/tracks/CWT_Dzhurla_2025_Taras.fit
tests/private/tracks/CWT_Dzhurla_2025.gpx
```

Report должен позволять вручную увидеть:

- исходный FIT и GPX course отдельными слоями;
- физически правдоподобный off-course/return участок как часть original FIT без
  автоматического `CORRUPTED` только из-за GPX;
- один нумерованный adjacent abnormal-transition run около records `5879..5881` с
  обеими исходными suspicious transitions в evidence;
- два отдельных нумерованных missing-position runs `6364..8031` и `9859..10410`;
- отсутствие invented solid geometry внутри обоих gaps;
- отсутствие номера на длинном gradual drift только потому, что он далеко от course,
  если текущий detector не создал для него отдельный diagnostic;
- понятное объяснение, почему общий detector result не содержит reconstructable
  corrupted interval.

Конкретные record indices private fixture не используются в production branches,
thresholds или UI special cases.

## Acceptance criteria

- `analyze FIT --html` использует shared template и исходный FIT без repair;
- optional `--course` добавляет только reference layer и summary;
- detector result побитово/структурно эквивалентен запуску без course;
- все top-level diagnostic regions имеют стабильные уникальные номера;
- номера совпадают на карте, в таблице и detail evidence;
- map/table navigation работает для mappable regions;
- non-mappable regions не исчезают из таблицы;
- missing/continuity boundaries никогда не соединяются solid track;
- таблица показывает machine-readable reasons и type-specific metrics;
- физически правдоподобный off-course movement не повышается до corruption;
- report не создаёт candidate/repaired geometry, FIT diff или output FIT;
- существующие analyze/repair reports не сломаны;
- public suite, private smoke при наличии, lint, format и mypy зелёные;
- CLI/README/architecture/test documentation синхронизированы с новым mode.

## Сознательно не реализовывать

- изменение detector thresholds или morphology;
- reconstruction/repair по выбранному номеру;
- ручное редактирование interval boundaries в HTML;
- запись исправленного FIT;
- использование GPX как corruption evidence;
- course deviation heatmap или nearest-course scan;
- OSM routing/map matching;
- DEM;
- web backend, upload или cloud report hosting;
- новый отдельный HTML template.
