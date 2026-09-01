# WarpBuster CLI Specification

## Общие требования

CLI должен быть удобен человеку и пригоден для автоматизации.

Использовать:
- стабильные exit codes;
- `--json`;
- `-v/-vv`;
- явные ошибки;
- отсутствие silent overwrite.

## 1. `warpbuster inspect`

Появляется в раннем milestone.

```bash
warpbuster inspect activity.fit
warpbuster inspect activity.gpx
```

Показывает:
- manufacturer/product, если доступно;
- start time;
- duration;
- record count;
- recorded distance;
- coordinate bounds;
- наличие position/time/altitude/HR/cadence/power;
- message types;
- developer fields summary.

Опционально позже:

```bash
warpbuster inspect activity.fit --json
warpbuster inspect activity.gpx --json
```

FIT report сохраняет FIT profile/CRC/message metadata. GPX report вместо них показывает
GPX version/creator и количество tracks/segments. Отсутствующие FIT-specific значения
для GPX не синтезируются.

## 2. `warpbuster analyze`

```bash
warpbuster analyze activity.fit
warpbuster analyze activity.gpx
```

Показывает:
- integrity status;
- confidence;
- impossible/suspicious transition count;
- corrupted intervals;
- bridge details;
- advisory geometry gap warnings;
- reasons.

```bash
warpbuster analyze activity.fit --json
warpbuster analyze activity.gpx --json
```

JSON schema должна быть стабильной внутри minor release.

Report показывает transition counts, robust baseline, thresholds, machine-readable
findings, corrupted intervals, bridge details и отдельные geometry warnings. Scope
`integrity_detection` содержит стадии `local_transitions`, `spoofing_islands`,
`one_sided_gnss_clusters`, `geometry_gap_diagnostics`, `vertical_plausibility`;
entry/exit evidence строится только из физической
непрерывности GNSS, а не из course.
Console и JSON также показывают нормализованные `sport/sub_sport` и выбранный threshold
profile. Для неизвестного sport generic profile не выдаёт `IMPOSSIBLE` только по скорости.
Для GPX разные `trkseg` являются разными continuity domains и не соединяются transition.

Geometry warning содержит границы records, chord/path metrics, confidence `LOW`, reasons
и `repair_eligible=false`. Он не меняет общий integrity status и exit code. Поэтому GPX
без timestamps может остаться `UNKNOWN` и одновременно содержать geometry warning.

One-sided diagnostics содержат boundaries, reconstructable flag, evidence counts,
normal context обоих anchors, direct bridge, positioned/tainted component counts и
machine-readable reasons. Принятый one-sided interval помечается `MEDIUM`; default
writer threshold его не применяет.

Vertical diagnostics содержат records, elapsed time, altitude delta, maximum absolute
vertical speed и reasons. Они не создают coordinate interval и явно помечаются как
неавторитетные для GNSS repair.

```bash
warpbuster analyze activity.fit --html report.html
warpbuster analyze activity.gpx --html report.html
```

`--html` записывает интерактивный local report и совместим с `--json`. Console/JSON
остаётся в stdout; в console mode дополнительно печатается путь к HTML. Existing report
не перезаписывается. Ошибка report destination возвращает `2`.

## 3. `warpbuster repair`

Появляется только после завершения detector milestones.

Dry-run остаётся planning mode для исходного FIT и reference GPX course. Команда без
`--dry-run` применяет выбранные interval candidates согласно `--min-confidence`.

### Dry run

```bash
warpbuster repair activity.fit --course race.gpx --dry-run
```

Показывает RepairPlan, не пишет FIT.

Опции:

```bash
warpbuster repair activity.fit --course race.gpx --dry-run --json
warpbuster repair activity.fit --course race.gpx --dry-run -v
warpbuster repair activity.fit --course race.gpx --dry-run --html preview.html
warpbuster repair activity.fit --course race.gpx --min-confidence medium
warpbuster repair activity.fit --course race.gpx --output out.fit --html repair.html
```

Report содержит anchor matches, direction, course span/speed, allocation method,
candidate coordinates, fields to change/recalculate, unresolved intervals и safety
flags. Course участвует только в reconstruction; `detection_used_course=false`.
Для one-sided candidate report дополнительно содержит общую длину anchor connectors,
reconstruction path, detected core и refined repair scope. Refinement выполняется только
после course-independent proof и требует устойчивого course corridor; итоговая geometry
проходит post-check локальных физических переходов.
Для каждого proposed anchor report также содержит directional NORMAL-context count и
blocking evidence. При unsafe anchors выводится bounded `mixed GNSS region`: границы,
число missing/suspicious/impossible evidence, proposed outer anchors и скорость прямого
outer bridge. Composite report дополнительно содержит ordered component states,
detected core ranges и отдельные `reconstruction_scope_ranges`. `PLAUSIBLE/UNKNOWN`
positioned components не включаются в updates; disjoint scope проверяется вместе с
внутренними connectors. Composite candidate имеет максимум `MEDIUM`, поэтому region
всегда `repair_eligible=false` при default threshold.

Plan statuses описывают coverage:
- `READY` — все intervals имеют HIGH candidate;
- `PARTIAL` — candidate существует только для части intervals или имеет более низкий
  confidence;
- `REFUSED` — reconstruction candidate отсутствует;
- `NOT_NEEDED` — reconstructable intervals отсутствуют.

`--min-confidence {low,medium,high}` выбирает доступные candidates указанного confidence
и выше; default — `high`. Dry-run возвращает `0`, если при выбранном threshold есть хотя
бы один candidate либо plan имеет `NOT_NEEDED`, иначе `3`. Write mode допускает partial
application: выбранные intervals записываются, остальные остаются неизменными. При
нулевом выборе output не создаётся и команда возвращает `3`. Preview и write report
перечисляют каждый interval с action, confidence, candidate availability, update count и
reasons. GPX activity вместо оригинального FIT и malformed course возвращают `2`.

### Explicit output

```bash
warpbuster repair activity.fit --course race.gpx --output out.fit
warpbuster repair activity.fit --course race.gpx --output out.fit --overwrite
```

Если `--output` не указан, используется `<stem>.fixed.fit`. Existing output по умолчанию
не перезаписывается. Явный `--overwrite` разрешает атомарную замену generated FIT и
`--html` только после validation и diff без unexpected changes. Source FIT никогда не
может быть destination.

### HTML report

Repair dry-run HTML показывает original track, course, выбранную candidate geometry и
все applied/skipped preview decisions. После фактической записи renderer повторно читает
validated output FIT и показывает actual repaired track и semantic diff.

HTML является одним локальным файлом с embedded report data и application code.
Интерактивная карта загружает Leaflet 1.9.4 с pinned `unpkg.com` URL и стандартные
OpenStreetMap raster tiles. Она поддерживает pan, zoom, scale, fit-to-track и layer
switching, start/end и markers через каждый 1 km. Missing coordinates разрывают solid polyline, а
отдельный dashed `Missing-data bridges` layer явно показывает неизвестные прямые между
доступными точками. Continuity boundaries не соединяются ни одним слоем. `--html` можно
сочетать с `--json` без добавления текста в JSON stdout.

Comparison table отдельно показывает embedded FIT distance, map geometry со straight
gap chords, solid known geometry без gaps, delta относительно course и elevation gain с
указанием источника. Missing-run table перечисляет records/count, anchors, elapsed time,
straight chord, recorded distance delta и bridge speed.
HTML path должен отличаться от FIT output path. Если HTML generation неожиданно падает
после успешного FIT publish, CLI явно сообщает, что FIT уже записан.

## 4. `warpbuster validate`

```bash
warpbuster validate activity.fixed.fit
```

Проверяет:
- декодирование;
- CRC, если доступно reader/writer layer;
- timestamps order;
- coordinate ranges;
- monotonic distance, где это ожидается;
- basic FIT consistency.

Valid report возвращает `0`, invalid — `4`. Доступен `--json`.

## 5. `warpbuster diff`

```bash
warpbuster diff original.fit fixed.fit
```

Показывает изменённые/сохранённые поля.

Report содержит changed records/fields, expected/unexpected changes, неизменность
definitions и preservation percentages для timestamps, sensors, developer и unknown
fields. `-v` показывает не более 20 field changes, `--json` — bounded detail до 200.
Structural или unexpected changes возвращают `4`.

## 6. Verbosity

`-v`:
- этапы pipeline;
- найденные intervals.

`-vv`:
- detector diagnostics;
- candidate bridges;
- thresholds/config values.

Console показывает не более 20 candidate diagnostics, 20 one-sided diagnostics и 20 geometry warnings. JSON сохраняет не более
`IntegrityConfig.diagnostic_max_candidate_details` деталей (default: 100), но всегда
содержит полные aggregate counters и число отброшенных деталей. Это не позволяет
диагностике потреблять неограниченную память на повреждённом файле.

Geometry warnings отдельно ограничены `IntegrityConfig.geometry_max_warnings`; JSON
сохраняет полные aggregate scan counters и число отброшенных warnings.

Не выводить тысячи records без отдельного debug flag.

## 7. Exit codes

- `0` — команда успешно выполнена, clean/valid where applicable;
- `1` — integrity anomalies detected;
- `2` — input invalid/unreadable;
- `3` — operation refused due to insufficient reconstruction confidence;
- `4` — validation failed;

HTML destination/no-overwrite errors относятся к `2`. При `--overwrite` существующий
HTML заменяется атомарно; в dry-run флаг действует только на HTML. Если FIT уже успешно опубликован,
но последующая HTML generation завершилась ошибкой, repair возвращает `3` и явно
указывает сохранённый FIT path.
