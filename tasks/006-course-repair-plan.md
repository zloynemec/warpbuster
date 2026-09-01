# Task 006 — GPX Course Matching + Dry Repair Plan

## Цель

После завершённого Integrity Detector научиться строить безопасный reconstruction plan по известному GPX, но пока НЕ писать FIT.

## Сделать

- GPX course reader;
- course polyline + cumulative distance;
- поиск match для trusted anchor before/after;
- учитывать направление и temporal order;
- защита от self-intersection/ambiguous match;
- reconstruction confidence;
- candidate coordinates для records corrupted interval;
- `RepairPlan`;
- `warpbuster repair ... --dry-run`;
- отдельные statuses `READY`, `PARTIAL`, `REFUSED`, `NOT_NEEDED`;
- console и JSON report с candidate coordinates и safety guarantees.

## Строгие правила

- GPX не влияет на corruption detection;
- trusted coordinates вне interval не меняются;
- timestamps не меняются;
- LOW/MEDIUM или ambiguous reconstruction не применяется автоматически;
- writer отсутствует: команда без `--dry-run` должна быть отклонена;
- без course координаты не восстанавливаются.

## Распределение по course

Не использовать только равномерное распределение по времени, если доступны более информативные сигналы.

Но любые speed/distance signals считать evidence, а не безусловной истиной.

## Acceptance Criteria

- основной Andromeda spoofing island + course получает HIGH-confidence repair candidate,
  а interval без
  безопасного course match остаётся unresolved;
- plan явно перечисляет records/fields, которые будут изменены;
- wrong-turn outside corrupted interval остаётся untouched;
- `--dry-run` не создаёт изменённый FIT.

## Не делать

- FIT writer;
- reconstruction без reference course;
- OSM/DEM routing;
- изменение timestamps, sensors или trusted coordinates;
- применение partial/ambiguous plan.

## Safety follow-up

Проверка того, что формальные trusted anchors не находятся внутри более широкого GNSS
failure, вынесена в завершённый `tasks/006a-trusted-anchor-safety.md`.
