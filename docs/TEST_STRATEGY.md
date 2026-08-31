# WarpBuster Test Strategy

## 1. Цель

Главный риск WarpBuster — не пропустить ошибку, а **испортить настоящий трек**.

Поэтому тестовая стратегия ориентирована прежде всего на false positives.

## 2. Уровни тестов

### Unit
- distance/geodesy;
- robust statistics;
- transition classification;
- bridge plausibility;
- confidence scoring;
- GPX segment matching.

### Integration
- FIT → ActivityData;
- analyze full activity;
- repair plan;
- FIT write/read round-trip;
- diff/validation.

### Acceptance
- private real activities;
- Garmin Connect/Strava manual compatibility вне CI.

## 3. Synthetic fixtures

Нужен генератор trajectories.

### clean_run
Последовательный бег, нормальная cadence/time.

Expected: CLEAN.

### single_spike
Одна точка улетает и возвращается.

Expected: corrupted short interval.

### spoof_island
Teleport out → 20 минут плавного ложного движения → teleport back.

Expected: единый island.

### wrong_turn
Постепенный уход на километры от hypothetical course.

Expected: CLEAN.

### loop
Настоящая петля/возврат.

Expected: CLEAN.

### trail_switchbacks
Частые резкие изменения heading.

Expected: CLEAN.

### fast_downhill
Высокая, но физически правдоподобная скорость.

Expected: CLEAN или максимум low suspicion, но не corrupted.

### gps_dropout
Position отсутствует N минут.

Expected: MISSING_GNSS interval.

### irregular_sampling
1s/5s/20s cadence mix.

Expected: detector не путает большой segment с teleport только из-за dt.

## 4. Private Andromeda fixture

Хранить локально, например:

`tests/private/andromeda/activity.fit`

`tests/private/andromeda/course.gpx`

Не коммитить без явного решения владельца данных.

Acceptance analyze:
- course НЕ передавать;
- основной spoof island найден;
- HIGH confidence;
- boundaries примерно совпадают с известным incident;
- tolerance сначала ±30s.

Acceptance repair:
- course передаётся только reconstruction;
- trusted records вне interval остаются неизменными;
- timestamps unchanged;
- sensor data unchanged;
- output FIT валиден;
- итоговая геометрия/дистанция разумны.

## 5. False-positive safety matrix

До появления repair synthetic regression suite обязан покрывать:

- постепенный wrong turn на километры;
- out-and-back и замкнутую петлю;
- tight switchbacks;
- быстрый непрерывный downhill;
- stop/restart и irregular sampling;
- длинный GPS dropout;
- короткий noisy drift;
- несколько правдоподобных pace regimes.

Для каждого сценария запрещён результат `CORRUPTED / HIGH` и запрещены corrupted
intervals. Wrong turn дополнительно обязан быть `CLEAN`. API detector-а не принимает
course: отклонение от GPX не является detector evidence.

## 6. Golden reports

Для synthetic fixtures можно хранить expected JSON reports.

Не golden-test-ить человекочитаемый console output посимвольно, если это не CLI contract.

## 7. Performance test

Fixture ~20k records.

Target MVP:
- `analyze` < 5 s на современном ноутбуке;
- memory bounded;
- отсутствие O(n²) full scan.

Worst-case regression с большим количеством impossible edges дополнительно проверяет,
что bridge candidate details ограничены конфигурацией, а aggregate counters не теряются.

## 8. FIT preservation regression

После repair сравнивать original vs fixed:

Expected unchanged:
- timestamp;
- HR;
- cadence;
- altitude;
- power;
- unrelated developer fields.

Expected changed:
- position в repaired interval;
- derived distance/speed fields where required;
- affected aggregates.

Любое неожиданное изменение должно появляться в diff.
