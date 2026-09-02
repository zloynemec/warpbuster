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

Чтение и инспекция FIT:

```bash
warpbuster inspect activity.fit
warpbuster inspect activity.fit --json
warpbuster inspect activity.gpx
warpbuster inspect activity.gpx --json
```

Локальный анализ физических переходов:

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
warpbuster repair activity.fit --course race.gpx --min-confidence medium
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

`repair --dry-run` строит course-based `RepairPlan`, но не изменяет и не создаёт FIT.
`READY` означает, что все corrupted intervals получили однозначные HIGH candidates;
`PARTIAL` означает, что candidate существует только для части intervals. Статус плана
описывает полноту reconstruction, а не запрет записи.
Перед course matching каждый proposed trusted anchor проходит независимую проверку
локального NORMAL-контекста. Если рядом продолжаются jumps или missing-position gaps,
anchor считается unsafe, а отчёт показывает bounded `mixed GNSS region`, внешние
диагностические anchors и прямой bridge. Такой регион остаётся `MEDIUM/LOW` и никогда не
становится auto-repairable только из-за подходящего course.

Для составного региона отчёт отдельно показывает detected cores, ordered
`POSITIONED/MISSING` components и точный reconstruction scope. Явный `MEDIUM` может
восстановить доказанно затронутые/tainted components и заполнить missing coordinates,
но физически правдоподобные или неизвестные positioned components остаются byte-identical.
Каждый connector к сохранённому component проходит physical post-check. Lossless writer
может заполнить missing coordinate только если соответствующие FIT fields присутствуют
в исходном message definition как invalid values.

Если impossible entry сопровождается missing-position gaps, но классический impossible
exit скрыт dropout-ом, detector выполняет отдельный bounded one-sided scan. Interval
создаётся только при stable outer anchors, plausible direct bridge и abnormal evidence
в каждом внутреннем positioned-компоненте. Course в этом proof rule не участвует.
Уверенность всегда не выше `MEDIUM`: default `HIGH` такой candidate пропускает, для
применения нужен явный `--min-confidence medium`.

Для one-sided reconstruction detected core остаётся неизменным audit evidence, но его
repair scope расширяется наружу до устойчивого configurable course corridor. Это не
меняет detector и не повышает confidence выше `MEDIUM`, зато локально плавные точки
внутри gradual drift больше не становятся trusted anchors. Candidate проходит короткий
входной connector, matched course span и выходной connector. Неравномерная distance/speed
allocation, создающая abnormal переходы, заменяется timestamp allocation; физически
невозможный итоговый candidate отклоняется.

Running profile отдельно ищет sustained и single-extreme vertical rates. Эти findings
помечаются как sensor-consistency warnings: они не создают coordinate interval, не
меняют integrity status и не дают writer права менять altitude или GNSS coordinates.

Отсутствующий GPS prefix/suffix не считается corruption. При явном
`--fill-missing-from-course` отдельный reconstruction provider может предложить
`MEDIUM` candidate, если длинный существующий GPS run целиком физически правдоподобен,
однозначно совпадает с course и согласован с recorded distance. Поэтому для применения
нужны сразу этот флаг и `--min-confidence medium`. Existing GPS coordinates не меняются,
а timestamps, sensors и embedded FIT distance сохраняются. Этот provider работает
независимо от обычного corruption repair; оба scope объединяются перед одной атомарной
записью. Внутренние clean gaps пока не заполняются.

Команда без `--dry-run` выбирает все доступные interval candidates с confidence не ниже
`--min-confidence`; default — `high`. Поэтому безопасная HIGH-часть `PARTIAL` plan может
быть записана, а unresolved и кандидаты ниже порога остаются неизменными. Значения
параметра: `low`, `medium`, `high`. Если не выбран ни один candidate, output не создаётся.
Writer создаёт `<stem>.fixed.fit` либо путь из `--output`, сохраняет исходные FIT frames,
пересчитывает CRC и проверяет output. Existing destination защищён по умолчанию;
`--overwrite` атомарно заменяет FIT и HTML после успешной validation. Исходный FIT этим
флагом перезаписать нельзя. Dry-run preview и
итоговый write report перечисляют каждый interval как `APPLIED` или `SKIPPED` с причиной.
`warpbuster validate` проверяет FIT/CRC и базовые invariants, а `warpbuster diff`
показывает expected/unexpected changes и preservation percentages.

### Интерактивный HTML-отчёт

`--html` создаёт один локальный файл с embedded activity data, summary, track geometry,
course/candidate, applied/skipped intervals, findings, speed/altitude/HR graphs и FIT
diff после записи. Отдельная таблица сравнивает embedded FIT distance, map geometry,
solid known geometry и elevation gain для original/course/repaired. Missing-position
runs перечисляются с anchors, временем, straight chord, recorded distance delta и bridge
speed. Missing-completion table отдельно показывает prefix/suffix action, course span,
connector, allocation, alignment error и сохранение FIT distance. Отдельная таблица
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
