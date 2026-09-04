<p align="center">
  <img src="docs/assets/warpbuster-logo.png" alt="WarpBuster logo" width="320">
</p>

# WarpBuster Core

WarpBuster — локальное Python-ядро и CLI для обнаружения и восстановления физически
невозможных GNSS/GPS-данных. FIT остаётся главным lossless форматом, а FIT и GPX можно
использовать как входные активности для inspection и detection.

Главная цель первой версии — **не «сделать красивый трек»**, а сначала доказать, что конкретный участок координат физически недостоверен, и только после этого разрешать реконструкцию.

## Главный принцип

> **Never modify plausible movement. Repair only demonstrably impossible GNSS data.**

Если бегун последовательно ушёл с маршрута на километры, развернулся, сделал петлю или побежал по незнакомой тропе — это настоящий трек и WarpBuster не должен его исправлять.

## Scope v0.1

В v0.1 входят:

- чтение FIT;
- чтение GPX activity без конвертации в FIT;
- нормализованная модель активности;
- CLI `inspect`, `analyze`, `repair`, `validate` и `diff`;
- поиск физически невозможных переходов;
- обнаружение длительных spoofing islands;
- bounded-детекция one-sided GNSS failure clusters с missing exit;
- advisory-предупреждения о возможных интерполированных GNSS gaps;
- confidence/reasons для каждого подозрительного интервала;
- опциональная реконструкция только уже доказанно повреждённых интервалов по известному GPX course;
- явное course-backed заполнение отсутствующих endpoint coordinates с максимумом `MEDIUM`;
- сохранение исходных timestamps и спортивной телеметрии;
- FIT validation/diff;
- console/JSON/HTML-отчёты.

Не входят:

- Garmin/COROS/Strava API;
- OAuth/webhooks;
- web UI;
- PostgreSQL/Redis;
- OSM routing;
- DEM;
- облачная синхронизация.

## Документы

- `AGENTS.md` — обязательные правила для Codex.
- `docs/PRODUCT_SPEC.md` — функциональное ТЗ v0.1.
- `docs/ARCHITECTURE.md` — архитектура ядра.
- `docs/DETECTION_MODEL.md` — модель Integrity Detector.
- `docs/CLI_SPEC.md` — CLI-контракт.
- `docs/TEST_STRATEGY.md` — тестирование и acceptance fixtures.
- `docs/DECISIONS.md` — зафиксированные архитектурные решения.
- `docs/MILESTONES.md` — порядок разработки.
- `tasks/` — задания, которые нужно отдавать Codex **строго по одному**.

## Как работать с Codex

Не просить Codex «реализовать WarpBuster v0.1».

Нужно брать следующий незавершённый файл из `tasks/` и давать его как самостоятельное задание. После выполнения:

1. проверить acceptance criteria;
2. запустить тесты;
3. просмотреть diff;
4. зафиксировать результат;
5. только затем переходить к следующему этапу.

Актуальное состояние milestones и отложенные задачи находятся в `ROADMAP.MD`.

## Разработка

Требуется Python 3.14 или новее.

```bash
git clone git@github.com:zloynemec/warpbuster.git
cd warpbuster
python3.14 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

Проверка CLI и импорта:

```bash
warpbuster --version
python -c "import warpbuster; print(warpbuster.__version__)"
```

OSM Manager устанавливается отдельно и не добавляет routing в Core:

```bash
python -m pip install -e "packages/osm-manager[dev]"
warpbuster-osm capabilities --json
warpbuster-osm ensure --gpx race.gpx
```

При запуске из корня проекта автоматически используется локальный `osm-manager.toml`;
указывать `--config` не требуется.

Полная документация и конфигурация:
[`packages/osm-manager/README.md`](packages/osm-manager/README.md) и
[`packages/osm-manager/osm-manager.example.toml`](packages/osm-manager/osm-manager.example.toml).

Изолированный Valhalla adapter устанавливается отдельно. Он готовит проверенный graph
cache, показывает зафиксированный trail-running profile и строит audited routes
по точному `graph_id`: один по умолчанию либо основной и до двух alternatives.

```bash
python -m pip install -e "packages/osm-routing[dev]"
warpbuster-osm-route prepare /absolute/path/to/manifest.json
warpbuster-osm-route profile show
warpbuster-osm-route profile show --json
warpbuster-osm-route route sha256:GRAPH_DIGEST --from 44.614065,33.736355 --to 44.604988,33.773734
warpbuster-osm-route route sha256:GRAPH_DIGEST --from 44.614065,33.736355 --to 44.604988,33.773734 --alternates 2 --json
```

Alternatives проходят отдельный audit и сравниваются по длине/edge-weight similarity.
Поиск неполный: один найденный путь не доказывает уникальность, а выбор для repair
остаётся будущим этапом. Подробности: [`packages/osm-routing/README.md`](packages/osm-routing/README.md).

Чтение и инспекция FIT:

Корректные FIT читаются строго, независимо от производителя. Для обнаруженного
в экспорте COROS определения `event.data` (message 21, field 3: `uint32`, размер
1 байт) поддержано ограниченное исключение: поле сохраняется как непрозрачные
байты, без интерпретации как `timer_trigger` и без изменения исходного определения.
Правило выбирается по структуре, не по названию часов или файла. Время, тип события
и группа таймера читаются обычным способом; учёт пауз не отключается.
Предупреждение видно в inspect/validate, JSON и HTML-отчётах. Reader, writer и diff
используют одинаковое правило, CRC остаётся обязательным. Другие ошибки размеров,
неизвестные developer definitions, обрывы и ошибки CRC по-прежнему блокируют чтение.
Это не универсальный permissive-режим и не гарантия поддержки всех экспортов COROS.

Task 011B: нормальные timer pauses больше не блокируют GPX-реконструкцию целиком.
Путь распределяется по пригодной FIT distance, затем по speed, затем — оценочно по
активному времени. Интеграл speed использует трапеции по активным длительностям
между records, без выключенного времени. Паузы объединяются и обрезаются границами
gap вместе с anchors; timestamps/events/sensors и число records не меняются.
Во время остановки продвижение по реконструированному пути отсутствует. Ненулевая
distance на полностью остановленном интервале — `pause_distance_conflict`, незакрытая
пауза внутри окна — `timer_state_unresolved`, отсутствие активного времени —
`no_active_time`; невозможная скорость за активное время —
`active_time_traversal_implausible`. Отказы локальны и не разрешают менять сохранённые
GPS-точки. Допуск на дрейф distance внутри паузы не вводился. HTML/JSON/console
показывают elapsed/paused/active seconds и allocation method. Наличие паузы не меняет
Integrity Detector и не является доказательством физической неподвижности.

```bash
warpbuster inspect activity.fit
warpbuster inspect activity.fit --json
warpbuster inspect activity.gpx
warpbuster inspect activity.gpx --json
```

Локальный анализ физических переходов:

Detector также проверяет физически недостижимый GNSS-хвост без здоровой правой опоры.
Доказательство `unreachable_tail` имеет максимум `MEDIUM`: для удаления требуется
`--min-invalidation-confidence medium`. GPX не участвует в доказательстве, и отказ
реконструкции не мешает удалить доказанно ошибочные координаты. Records, время и
датчики сохраняются. В отчёте вместо отсутствующего bridge показан reachability proof.
Подробности и ограничения: [Task 011A](tasks/011a-unreachable-terminal-gnss.md).

```bash
warpbuster analyze activity.fit
warpbuster analyze activity.fit -v
warpbuster analyze activity.fit -vv
warpbuster analyze activity.fit --json
warpbuster analyze activity.fit --html
warpbuster analyze activity.fit --html analysis.html
warpbuster analyze activity.fit --course race.gpx --html detector.html
warpbuster analyze activity.fit --course race.gpx --html detector.html --overwrite
warpbuster analyze activity.gpx
warpbuster analyze activity.gpx --json
warpbuster repair activity.fit --course race.gpx --dry-run
warpbuster repair activity.fit --course race.gpx --dry-run --json
warpbuster repair activity.fit --course race.gpx --dry-run --html
warpbuster repair activity.fit --course race.gpx --dry-run --html preview.html
warpbuster repair activity.fit --dry-run --html
warpbuster repair activity.fit --min-invalidation-confidence medium --html
warpbuster repair activity.fit --course race.gpx --min-invalidation-confidence medium --min-confidence medium
warpbuster repair activity.fit --course race.gpx --fill-missing-from-course --min-confidence medium
warpbuster repair activity.fit --course race.gpx --output activity.fixed.fit --html repair.html
warpbuster repair activity.fit --course race.gpx --output activity.fixed.fit --html repair.html --overwrite
warpbuster validate activity.fixed.fit
warpbuster diff activity.fit activity.fixed.fit
```

Exit code `1` означает, что найдены `SUSPICIOUS` или `IMPOSSIBLE` переходы;
нечитаемый FIT возвращает `2`.

`analyze` выбирает thresholds по нормализованному виду активности. Для `running`
используется отдельный консервативный профиль; неизвестный sport не получает
`CORRUPTED / HIGH` только из-за высокой apparent speed.

`-v` показывает стадии pipeline, а `-vv` дополнительно объясняет активные thresholds,
границы bounded island search и результаты проверки bridge-кандидатов. Детали
кандидатов ограничены конфигурацией; полные агрегатные счётчики остаются в JSON.

`analyze` также показывает `LOW` geometry warnings для длинных почти идеально прямых
участков, похожих на интерполяцию. Такое предупреждение не меняет integrity status или
exit code, не создаёт corrupted interval и всегда имеет `repair_eligible=false`.

`repair` сначала независимо от GPX строит маску достоверности координат и единый
список пустот: исходные missing, доказанно invalidated и mixed. Каждая пустота получает
свои непосредственные сохранённые anchors и ограниченный локальный контекст.
Правдоподобное отклонение от курса сохраняется и не блокирует удалённые пустоты.
Самый длинный GPS run, общая дистанция и GPX-коридор больше не определяют область
замены. Короткий preserved/UNKNOWN component разделяет edit scopes.

`--course` необязателен. Без него разрешённые invalidations всё равно могут записать
очищенный FIT; пустоты получают `no_course`, routing не запускается.
`--fill-missing-from-course` разрешает GPX-реконструкцию всех prefix/internal/suffix
пустот, включая mixed. Для начала/конца принимаются соответствующие endpoints GPX
как `course_assumption`, без дополнительного флага подтверждения. Без opt-in обычный
repair обрабатывает только внутренние полностью доказанно corrupted scopes; исходные
missing и endpoint completion остаются видимыми skipped targets.

Два независимых порога:

- `--min-invalidation-confidence {high,medium}`, default `high`: разрешает удалить
  координаты только при независимом detector proof достаточного уровня. Course,
  `SUSPICIOUS`, `UNKNOWN` и diagnostic `TAINTED` сами по себе таким proof не являются.
- `--min-confidence {high,medium,low}`, default `high`: выбирает reconstruction
  candidates. `LOW` никогда не применяется, даже при значении `low`.
  Original-missing, mixed и endpoint-assumption candidates имеют максимум `MEDIUM`.

Для чистого missing completion достаточно `--fill-missing-from-course
--min-confidence medium`. Для доказанной `MEDIUM` corruption дополнительно нужен
`--min-invalidation-confidence medium`; снижение порога пути не разрешает удаление.
One-sided detector остаётся прежним: independent impossible entry, stable outer
anchors, plausible bridge и evidence по компонентам; его scope больше не расширяется
до GPX-коридора.

`--dry-run` не создаёт FIT и показывает invalidations, candidates и локальные причины
отказа. `READY` означает отсутствие unresolved gaps, но не автоматическое разрешение
всех candidates: selection применяется отдельно. `PARTIAL` разрешает независимую
запись доступных изменений. `REFUSED` означает отсутствие применимых вариантов,
`NOT_NEEDED` — отсутствие пустот и разрешённой очистки. В dry-run exit code `0`
означает выбранные изменения либо no-op; `3` — ни одного разрешённого изменения при
имеющихся gaps. Ошибки входных данных/аргументов дают `2`.

Writer одним атомарным проходом меняет только разрешённые position fields и явно
поддержанные coordinate-dependent aggregates. При отказе от replacement разрешённая
invalidation остаётся: плохие координаты записываются как FIT invalid values.
Если нет ни invalidations, ни выбранных candidates, FIT не создаётся.
Для выбранных кандидатов отсутствующие в FIT schema position fields добавляются
как native sint32 поля (Task 011C). Временная definition действует только на
конкретную record и сразу сменяется исходной; соседние records не расширяются.
Timestamps, sensors, altitude, unknown/developer fields и сохранённые координаты
не меняются. Размер контейнера и CRC пересчитываются, FIT diff явно показывает
число добавленных coordinate fields и изменение definitions. Это возможность
записать принятый кандидат, а не обход проверок дистанции, скорости и GPX matching.

Recorded distance и speed квалифицируются отдельно, без GPX: пропуски, reset/zero и
физически невозможные значения диагностируются. Для allocation проверяются сначала
пригодные distance deltas, затем integrated speed; расхождение первого источника с
путём не запрещает проверить второй. Допуск длины — `max(3 m, 0.15 × path length)`.
Time-only interpolation не обходит противоречащие пути правдоподобные измерения.

В успешно восстановленном scope доказанно невозможные приращения distance исправляются
по новой геометрии независимо от origin: original-missing, invalidated или mixed.
Используются физические пределы activity profile, а не расхождение с GPX. Правдоподобный
distance сохраняется; неизвестное происхождение distance/speed не объявляется независимым
измерением. Partial/non-monotonic distance streams не переписываются с угадыванием reset.
Другие unresolved gaps не запрещают локальную коррекцию: их исходные приращения сохраняются,
ни хорда, ни нулевая длина вместо пустоты не придумываются. Downstream cumulative distance
и поддержанные lap/session totals/average speed получают только накопленную локальную
поправку. Record speed, sensors и altitude сохраняются. Незавершённая геометрия или
противоречивый сохранённый сигнал дают `quality=uncertain`, даже после локальной коррекции.
Перед публикацией writer повторно читает временный FIT и проверяет фактические координаты,
стыки, timestamps и все запланированные изменения метрик.

Default output — `<stem>.fixed.fit`; путь можно задать через `--output`.
`--overwrite` заменяет только выходные FIT/HTML, а не исходный FIT или GPX.
`warpbuster validate` проверяет FIT/CRC; `warpbuster diff` показывает разрешённые и
неожиданные изменения, сохранность timestamps/sensors/unknown/developer fields.

### Пакетный repair по CSV

Список пяти тестовых пар — `tests/repair_pairs.csv`, колонки `fit,gpx`.
Пути разрешаются относительно CSV, а не текущего рабочего каталога; абсолютные пути
тоже поддерживаются. Приватные FIT/GPX остаются в ignored `tests/private/tracks/`.

Из корня проекта:

```bash
.venv/bin/python scripts/repair_pairs.py tests/repair_pairs.csv
```

Для каждой пары последовательно запускается `python -m warpbuster repair` с
`--overwrite --html --fill-missing-from-course --min-invalidation-confidence medium
--min-confidence medium`. Используется тот же Python, которым запущен скрипт;
WarpBuster должен быть установлен в этом окружении.

Результаты рядом с исходным FIT: `<stem>.fixed.fit` и `<stem>.repair.html`.
Имеющиеся выходные файлы перезаписываются; исходные FIT/GPX не меняются.
Ошибка или отсутствие файлов одной пары не останавливает остальные: в конце выводится
сводка. Код завершения: `0` — все команды успешны, `1` — были ошибки пар, `2` —
некорректный CSV, `130` — прерывание. Успешная команда repair может дать частичный
результат: степень восстановления смотри в её HTML-отчёте.

По завершении создаётся `tests/repair_pairs.reports/index.html`. Вверху указаны дата и
время начала всего пакетного запуска с локальным часовым смещением, а не время
завершения генерации отчёта. Ниже расположена сводная таблица:

- имя FIT со ссылкой на полный отчёт;
- километры **стало / было**;
- средний темп **стал / был**, `мин:сек/км`;
- проблемы **исправлено / найдено** — число полностью заполненных gaps G1, G2…
  относительно всех gaps. Invalidation без заполнения не считается исправленным разрывом.

Дистанция и темп берутся из исходного/выходного FIT тем же расчётом, что в полном отчёте,
не из длины GPX или средней величины покилометровых темпов. Частичный результат и
неопределённость метрик подписываются явно; отсутствующие значения — `—`, не нули.
При ошибке строка остаётся в таблице, но старый отчёт не используется как новый результат.

Каталог содержит `index.html` и копии подробных HTML; ссылки относительные, поэтому
его можно целиком перенести или раздавать локальным HTTP-сервером. FIT/GPX в него не
копируются. При совпадении имён FIT из разных директорий копии получают порядковый префикс.
Для уже запущенного сервера можно указать его каталог:

```bash
.venv/bin/python scripts/repair_pairs.py tests/repair_pairs.csv --report-dir /path/to/served-reports
```

Сводка и копии перезаписываются при каждом запуске. Default-каталоги `*.reports/`
исключены из Git: отчёты содержат приватные данные. `--report-dir` не запускает сервер.

Проверить пути и показать команды **без запуска repair и записи FIT/HTML**:

```bash
.venv/bin/python scripts/repair_pairs.py tests/repair_pairs.csv --dry-run
```

Повторы FIT и пересечения выходных путей с другими входами (включая сводку) запрещены
ещё до запуска. `--dry-run` не создаёт ни сводку, ни каталог отчётов.
Скрипт не изменяет detector/reconstruction и не добавляет собственную логику repair.

### Интерактивный HTML-отчёт

`--html` создаёт один локальный файл с embedded activity data, summary, track geometry,
course/candidate, applied/skipped intervals, findings, speed/altitude/HR graphs и FIT
diff после записи. Отдельная таблица сравнивает embedded FIT distance, map geometry,
solid known geometry и elevation gain для original/course/repaired. Missing-position
runs перечисляются с anchors, временем, straight chord, recorded distance delta и bridge
speed. Единая таблица пустот связана с маркерами **G1, G2, …** на карте: origin,
invalidation/path confidence, actual anchors, context, applied/skipped/unresolved и
причины. Раскрываемый «Proof / path» показывает независимый proof scope, GPX branch,
source hash, длины, ошибки локальной проекции, connectors и качество allocation.
При unresolved geometry или distance signal блок темпа помечен «distance / pace uncertain».
Отчёт разделяет исходную диагностику, состояние геометрии пустот и состояние метрик;
верхняя дистанция после записи берётся из выходного FIT. На карте сохранённые координаты
серые, реконструированные участки и их стыки синие. Километровые метки основаны на FIT
distance без дополнительных знаков; uncertainty остаётся в сводке и подсказках меток.
G-номера — отдельные ID пустот, не километровые границы.
Отдельная таблица
one-sided GNSS clusters показывает boundaries, confidence,
reconstructability, anchor context, bridge, tainted components и reasons.

В `analyze` тот же шаблон показывает только исходную activity geometry и не запускает
reconstruction или FIT writer. Detector intervals, оставшиеся соседние
`SUSPICIOUS/IMPOSSIBLE` transitions, one-sided diagnostics, geometry/vertical warnings и
missing-position runs получают стабильные номера на карте и в общей таблице
расшифровки. `--course race.gpx` разрешён для FIT только вместе с `--html`: GPX
показывается отдельным `Reference only` слоем после завершения detection и не влияет на
status, confidence, reasons, repair eligibility или JSON detector report. Отклонение от
course само по себе не получает номер и не считается corruption.

Analysis report также рассчитывает по исходному FIT беговую сводку: средний темп,
timer time, total ascent/descent, покилометровый темп и покилометровые набор/спуск.
Расчёт использует только фактические FIT timestamps, recorded distance и altitude; GPX
course и отсутствующая candidate geometry в эти показатели не подмешиваются.

После фактической записи отдельный блок показывает средний темп исправленного FIT,
время, total ascent/descent и покилометровую pace histogram. Высотная гистограмма содержит
две соседние колонки
на каждом recorded-distance километре: сумма всех подъёмов и сумма всех спусков.
Device totals могут отличаться от raw unsmoothed колонок; это различие поясняется в
примечании к отчёту. Неполный последний километр показывается отдельно с нормализованным
темпом.
Report открывается напрямую с диска. Интерактивная карта использует Leaflet 1.9.4 с CDN
и стандартные OpenStreetMap tiles, поэтому для basemap нужен интернет. Pan, wheel/+/- zoom,
scale, fit-to-track, start/end, markers через каждый 1 km и переключение слоёв доступны
на самой карте.
Solid track разрывается на missing GNSS coordinates; отдельный включённый по умолчанию
пунктирный `Missing-data bridges` показывает связь между доступными точками, не выдавая
неизвестную прямую за записанную или восстановленную geometry. Границы continuity никогда
не соединяются.

При открытии отчёта браузер обращается к `unpkg.com` и `tile.openstreetmap.org`: эти
сервисы видят IP и область запрошенных tiles. Coordinates и telemetry остаются embedded
в локальном HTML и не отправляются как отдельный API payload. Attribution OpenStreetMap
всегда показана на карте.

`--json` и `--html` можно использовать вместе: JSON остаётся в stdout. Existing HTML
не перезаписывается без явного `--overwrite`. В режиме `analyze` этот флаг атомарно
заменяет только HTML report и никогда не изменяет source FIT; без `--html` он является
ошибкой. Сам HTML содержит coordinates и telemetry, поэтому его следует считать
приватным файлом.

Значение пути у `--html` опционально. Bare `analyze ... --html` создаёт рядом с source
activity файл `<stem>.analyze.html`, а bare `repair ... --html` —
`<stem>.repair.html`. Без самого флага HTML не создаётся; явный путь продолжает иметь
приоритет над default.

Полный набор проверок:

```bash
python -m pytest
python -m ruff check .
python -m ruff format --check .
python -m mypy src tests
```

На текущем этапе реализованы чтение FIT, инспекция, локальный анализ соседних GNSS
observations, GPX activity input, bounded-поиск spoofing islands по impossible entry/exit
и plausible bridge, geometry gap diagnostics, а также false-positive regressions и
bounded diagnostics. M5 также добавляет GPX course matching и dry-run RepairPlan.
Task 006A добавляет course-independent trusted-anchor safety gate, а Task 006B —
missing-exit proof rule и explicit-MEDIUM reconstruction. Task 006C добавляет
component-wise composite repair, а Task 006D — отдельное opt-in заполнение endpoint
missing coordinates. Фактическая запись FIT,
validation и diff реализованы в M6. M7 добавляет interactive HTML reports и завершает
package/release stabilization. Private Andromeda regression подтверждает HIGH repair
основного interval, course-independent MEDIUM core `3627..3700`, refined repair scope
`3582..3741` и неизменный unresolved mixed region.
