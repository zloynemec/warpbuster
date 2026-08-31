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
```

## 2. `warpbuster analyze`

```bash
warpbuster analyze activity.fit
```

Показывает:
- integrity status;
- confidence;
- impossible/suspicious transition count;
- corrupted intervals;
- bridge details;
- reasons.

```bash
warpbuster analyze activity.fit --json
```

JSON schema должна быть стабильной внутри minor release.

Report показывает transition counts, robust baseline, thresholds, machine-readable
findings, corrupted intervals и bridge details. Scope `integrity_detection` содержит
стадии `local_transitions` и `spoofing_islands`; entry/exit evidence строится только из
физической непрерывности GNSS, а не из course.
Console и JSON также показывают нормализованные `sport/sub_sport` и выбранный threshold
profile. Для неизвестного sport generic profile не выдаёт `IMPOSSIBLE` только по скорости.

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
