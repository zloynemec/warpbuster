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
- `warpbuster repair ... --dry-run`.

## Строгие правила

- GPX не влияет на corruption detection;
- trusted coordinates вне interval не меняются;
- timestamps не меняются;
- LOW/MEDIUM ambiguous reconstruction не применяется автоматически.

## Распределение по course

Не использовать только равномерное распределение по времени, если доступны более информативные сигналы.

Но любые speed/distance signals считать evidence, а не безусловной истиной.

## Acceptance Criteria

- Andromeda + course строит HIGH-confidence repair plan;
- plan явно перечисляет records/fields, которые будут изменены;
- wrong-turn outside corrupted interval остаётся untouched;
- `--dry-run` не создаёт изменённый FIT.
