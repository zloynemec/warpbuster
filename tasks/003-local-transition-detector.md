# Task 003 — Local Physical Transition Detector

## Цель

Находить очевидные физически невозможные переходы между соседними GNSS observations.

## Сделать

- geodesic/haversine utility;
- `IntegrityConfig`;
- robust baseline stats;
- transition model;
- classification: normal / suspicious / impossible;
- apparent speed с корректным `dt`;
- missing-position handling;
- `warpbuster analyze`;
- console + JSON report;
- machine-readable reasons.

## Требование к thresholds

- conservative defaults;
- absolute physical impossibility отдельно от relative anomaly;
- все thresholds в config;
- никакого course.

## Synthetic tests

- clean run;
- single huge spike;
- irregular sampling;
- missing position;
- fast but plausible segment.

## Не делать

- объединение вход/выход в long island;
- GPX;
- repair.

## Acceptance Criteria

- `1 km / 1 sec` reliably impossible;
- clean synthetic run CLEAN;
- irregular cadence не даёт false teleport;
- Andromeda показывает как минимум физически невозможные transition(s);
- анализ работает без GPX.
