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

```bash
warpbuster repair activity.fit --course race.gpx
```

Default output:

`activity.fixed.fit`

Запрещён silent overwrite original.

### Dry run

```bash
warpbuster repair activity.fit --course race.gpx --dry-run
```

Показывает RepairPlan, не пишет FIT.

### Explicit output

```bash
warpbuster repair activity.fit --course race.gpx --output out.fit
```

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

## 5. `warpbuster diff`

```bash
warpbuster diff original.fit fixed.fit
```

Показывает изменённые/сохранённые поля.

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
