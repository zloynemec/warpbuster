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
`integrity_detection` содержит стадии `local_transitions`, `spoofing_islands` и
`geometry_gap_diagnostics`; entry/exit evidence строится только из физической
непрерывности GNSS, а не из course.
Console и JSON также показывают нормализованные `sport/sub_sport` и выбранный threshold
profile. Для неизвестного sport generic profile не выдаёт `IMPOSSIBLE` только по скорости.
Для GPX разные `trkseg` являются разными continuity domains и не соединяются transition.

Geometry warning содержит границы records, chord/path metrics, confidence `LOW`, reasons
и `repair_eligible=false`. Он не меняет общий integrity status и exit code. Поэтому GPX
без timestamps может остаться `UNKNOWN` и одновременно содержать geometry warning.

```bash
warpbuster analyze activity.fit --html report.html
```

HTML добавляется отдельным milestone.

## 3. `warpbuster repair`

Появляется только после завершения detector milestones.

Dry-run остаётся planning mode для исходного FIT и reference GPX course. На M6 команда
без `--dry-run` применяет только полный `READY` plan.

### Dry run

```bash
warpbuster repair activity.fit --course race.gpx --dry-run
```

Показывает RepairPlan, не пишет FIT.

Опции:

```bash
warpbuster repair activity.fit --course race.gpx --dry-run --json
warpbuster repair activity.fit --course race.gpx --dry-run -v
warpbuster repair activity.fit --course race.gpx --min-confidence medium
```

Report содержит anchor matches, direction, course span/speed, allocation method,
candidate coordinates, fields to change/recalculate, unresolved intervals и safety
flags. Course участвует только в reconstruction; `detection_used_course=false`.
Для каждого proposed anchor report также содержит directional NORMAL-context count и
blocking evidence. При unsafe anchors выводится bounded `mixed GNSS region`: границы,
число missing/suspicious/impossible evidence, proposed outer anchors и скорость прямого
outer bridge. Эти данные диагностические; region всегда `repair_eligible=false`.

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
```

Если `--output` не указан, используется `<stem>.fixed.fit`. Existing output никогда не
перезаписывается. Запись атомарно публикуется только после validation и diff без
unexpected changes.

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

Console показывает не более 20 candidate diagnostics и 20 geometry warnings. JSON сохраняет не более
`IntegrityConfig.diagnostic_max_candidate_details` деталей (default: 100), но всегда
содержит полные aggregate counters и число отброшенных деталей. Это не позволяет
диагностике потреблять неограниченную память на повреждённом файле.

Geometry warnings отдельно ограничены `IntegrityConfig.geometry_max_warnings`; JSON
сохраняет полные aggregate scan counters и число отброшенных warnings.

Не выводить тысячи records без отдельного debug flag.

## 7. Exit codes

Предварительно:

- `0` — команда успешно выполнена, clean/valid where applicable;
- `1` — integrity anomalies detected;
- `2` — input invalid/unreadable;
- `3` — operation refused due to insufficient reconstruction confidence;
- `4` — validation failed;
- `10` — internal/unexpected error.

Перед публичным release значения зафиксировать.
