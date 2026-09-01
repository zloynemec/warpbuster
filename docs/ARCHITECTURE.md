# WarpBuster Core — Architecture v0.1

## 1. Общая схема

```text
FIT ──► FIT Adapter ──┐
                     ├──► ActivityData
GPX ──► GPX Adapter ──┘
                              ├──► Inspect / Reports
                              │
                              ▼
Integrity Detector
                              │
                              ▼
                       IntegrityReport
                              ├── CLEAN
                              ├── UNKNOWN
                              ├── advisory geometry warnings
                              └── CORRUPTED intervals
                                      │
                                      ▼
                         Reconstruction Provider (optional)
                                      │
                                      ▼
                                Repair Plan
                                      │
                                      ▼
                              FIT Patch/Writer
                                      │
                                      ▼
                              Validation + Diff
```

## 2. Package layout

```text
src/warpbuster/
├── cli.py
├── config.py
├── models/
│   ├── activity.py
│   ├── integrity.py
│   └── reconstruction.py
├── fit/
│   ├── reader.py
│   ├── writer.py
│   ├── preserve.py
│   ├── diff.py
│   └── validate.py
├── gpx/
│   ├── reader.py
│   └── course.py
├── geo/
│   ├── distance.py
│   └── trajectory.py
├── integrity/
│   ├── detector.py
│   ├── transitions.py
│   ├── reachability.py
│   ├── islands.py
│   └── scoring.py
├── reconstruction/
│   ├── base.py
│   └── course.py
└── report/
    ├── console.py
    ├── json.py
    └── html.py
```

Структура может уточняться, но separation of concerns обязателен.

## 3. ActivityData

Нормализованная модель должна быть vendor-neutral.

Минимальный `ActivityRecord`:
- index;
- timestamp;
- latitude/longitude nullable;
- altitude nullable;
- distance nullable;
- speed nullable;
- heart_rate nullable;
- cadence nullable;
- power nullable;
- ссылка/идентификатор исходной observation;
- continuity id, запрещающий переходы между явно раздельными сегментами.

Дополнительно ActivityData хранит:
- laps;
- sessions;
- events;
- raw/preservation metadata.

## 4. FIT Adapter

Reader отвечает только за:
- parse/decode;
- normalized mapping;
- preservation metadata.

Integrity Detector не должен импортировать FIT SDK напрямую.

Это позволяет тестировать detector на синтетическом `ActivityData`.

## 4A. GPX Activity Adapter

Reader нормализует только activity tracks (`trk/trkseg/trkpt`). GPX `rte`/`wpt` и
vendor extensions не входят в Task 005A. Каждый `trkseg` получает отдельный continuity
id, поэтому detector не считает расстояние между разными сегментами teleport.

Это отдельная роль от GPX course в Reconstruction. GPX activity adapter не создаёт FIT
и не предоставляет course detector-у.

## 5. Integrity Detector

Pure-ish service:

`ActivityData + IntegrityConfig -> IntegrityReport`

Не парсит FIT или GPX самостоятельно.
Не принимает course и не пишет файлы.
Не обращается в сеть.

После authoritative physical stages выполняется отдельный bounded geometry diagnostic
pass. Он ищет длинные, плотно sampled, почти collinear участки и добавляет только
`LOW` warning. Geometry warning не участвует в вычислении integrity status, не создаёт
`CorruptedInterval` и всегда имеет `repair_eligible=false` в report. Это позволяет
показать возможную интерполяцию без нарушения главного инварианта detector-а.

Diagnostic pass использует только геометрию самой activity и continuity ids. Course,
OSM, DEM и vendor attribution в него не входят. Все thresholds и scan bounds находятся
в `IntegrityConfig`; число retained warnings ограничено, aggregate counters сохраняются.

## 6. Reconstruction

Интерфейс provider-а должен позволить позже добавить:

- `CourseReconstructionProvider`
- `OSMReconstructionProvider`
- `TerrainReconstructionProvider`

v0.1 содержит только `CourseReconstructionProvider`. Он получает уже завершённый
`IntegrityReport`; course не передаётся обратно в detector.

Перед GPX matching выполняется course-independent safety gate. Для before-anchor
сканируются последовательные переходы наружу назад, для after-anchor — вперёд.
Требуется configurable число подряд идущих `NORMAL` transitions; missing position,
continuity boundary, activity boundary или любой non-normal transition останавливает
bounded scan. Поэтому record рядом с ещё одним jump нельзя назвать trusted только потому,
что исходный island detector выбрал его границей.

Если один из anchors unstable, соседние `IMPOSSIBLE`/`SUSPICIOUS` transitions и
missing-position records объединяются в bounded `MixedGnssRegion` без использования
course. Внешние stable anchors и physically plausible direct bridge повышают только
качество диагностики до `MEDIUM`; они не доказывают corruption всех правдоподобных
coordinates внутри региона и не дают repair eligibility.

GPX course reader принимает `trk/trkseg/trkpt` и `rte/rtept`, сохраняет границы
continuous segments и строит cumulative distance. Trusted anchors проецируются на
polyline с configurable tolerance. Candidate pair обязан находиться на одном segment,
соблюдать temporal traversal order и давать физически правдоподобную course speed.
Несколько равноценных путей дают unresolved ambiguity, а не произвольный выбор.

Распределение records по matched span выбирает наиболее информативный пригодный signal:
recorded distance, integrated speed, timestamps или record order. Distance/speed
проверяются на согласованность с course length и остаются evidence, а не истиной.

## 7. Repair Plan

До записи файла reconstruction формирует декларативный plan:
- affected interval;
- coordinates to replace;
- fields to recalculate;
- confidence;
- reasons;
- warnings.

`--dry-run` должен останавливаться на этой стадии.

Plan имеет status:
- `READY` — все detected intervals имеют HIGH eligible candidates;
- `PARTIAL` — только часть intervals безопасно реконструирована;
- `REFUSED` — ни один interval нельзя применить;
- `NOT_NEEDED` — detector не нашёл reconstructable corruption.

Статус plan описывает coverage и не блокирует writer сам по себе. Candidate updates
содержат только records внутри interval; timestamps и trusted records неизменны.

## 8. FIT Writer

Writer получает:
- original FIT representation;
- RepairPlan.

Writer не должен самостоятельно решать, какие координаты плохие.

v0.1 writer выбирает available interval candidates с confidence не ниже invocation
threshold (`HIGH` по умолчанию). `PARTIAL` plan разрешён: выбранные intervals
применяются, unresolved и candidates ниже threshold пропускаются и перечисляются в
report. Writer повторно читает original raw bytes и изменяет in-place только fixed-width
scalar payload полей, явно перечисленных write policy.
Definitions, порядок/число messages, unknown fields/messages и developer payload не
перекодируются. Размер FIT остаётся прежним; footer CRC пересчитывается.

После coordinate patch cumulative `record.distance` корректируется только через edges,
касающиеся changed coordinates: исходный increment заменяется geodesic increment по
repaired geometry, а накопленная correction переносится дальше. Поддерживаемые
`lap/session.total_distance` и existing average-speed summary fields получают ту же
correction. Record speed не меняется без доказанного provenance: он может приходить от
footpod или sensor fusion и не обязан зависеть от GNSS.

Output сначала пишется во временный файл в destination directory. До atomic publish он
обязан пройти CRC decode, normalized validation и semantic diff с нулём unexpected field
changes. Existing destination не перезаписывается даже при race.

## 8A. Validation + Diff

`validate` проверяет strict decode/CRC, наличие records, порядок timestamps, coordinate
ranges и отсутствие distance regression внутри continuity segment.

`diff` сопоставляет source messages по global number/type/occurrence, отдельно проверяет
неизменность definitions и считает changed records/fields. Preservation metrics
показываются для timestamps, sensors, developer fields, unknown fields и всех fields;
неразрешённое изменение помечается unexpected.

## 9. Reporting

Console/JSON/HTML используют один и тот же доменный `IntegrityReport` / `RepairReport`.

Не дублировать detection logic в renderer-ах.

## 10. Configuration

Все detector thresholds — в `IntegrityConfig`.

Config должен быть сериализуемым и пригодным для:
- default profile;
- тестов;
- будущих sport-specific profiles.

## 11. Determinism

Одинаковые:
- input;
- config;
- version

должны давать одинаковый report и repair plan.
