# Task 008 — HTML Report + v0.1 Stabilization

## Цель

Сделать v0.1 удобной для диагностики и ручного тестирования.

## Сделать

`warpbuster analyze ... --html report.html`

и/или repair report.

Минимальный HTML:
- summary;
- integrity status/confidence;
- map;
- original vs repaired track;
- optional course;
- anomalies/интервалы;
- speed graph;
- altitude;
- HR при наличии;
- reasons;
- FIT diff summary.

Отчёт должен открываться локально.

## Финальная стабилизация

- CLI help;
- README;
- example workflow;
- packaging;
- performance check;
- clean install check;
- full regression suite.

## Definition of Done

Свериться с `docs/PRODUCT_SPEC.md` и `docs/MILESTONES.md`.

## Не делать

- cloud;
- Garmin/COROS/Strava integration;
- OSM/DEM;
- web backend/frontend.

Это отдельная следующая версия.
