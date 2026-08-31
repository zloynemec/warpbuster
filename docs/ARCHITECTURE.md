# WarpBuster Core — Architecture v0.1

## 1. Общая схема

```text
FIT
 │
 ▼
FIT Adapter
 │
 ▼
ActivityData
 │
 ├──────────────► Inspect / Reports
 │
 ▼
Integrity Detector
 │
 ▼
IntegrityReport
 │
 ├── CLEAN
 │
 ├── UNKNOWN
 │
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
- ссылка/идентификатор исходного FIT record.

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

## 5. Integrity Detector

Pure-ish service:

`ActivityData + IntegrityConfig -> IntegrityReport`

Не читает GPX.  
Не пишет FIT.  
Не обращается в сеть.

## 6. Reconstruction

Интерфейс provider-а должен позволить позже добавить:

- `CourseReconstructionProvider`
- `OSMReconstructionProvider`
- `TerrainReconstructionProvider`

v0.1 содержит только course provider.

## 7. Repair Plan

До записи файла reconstruction формирует декларативный plan:
- affected interval;
- coordinates to replace;
- fields to recalculate;
- confidence;
- reasons;
- warnings.

`--dry-run` должен останавливаться на этой стадии.

## 8. FIT Writer

Writer получает:
- original FIT representation;
- RepairPlan.

Writer не должен самостоятельно решать, какие координаты плохие.

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
