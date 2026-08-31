# Task 007 — FIT Writer, Preservation, Validate, Diff

## Цель

Безопасно применить RepairPlan к исходному FIT.

## Сделать

- FIT patch/write strategy;
- output default `<stem>.fixed.fit`;
- no silent overwrite;
- обновить position только по plan;
- пересчитать необходимые distance/speed/summary fields;
- сохранить timestamps;
- сохранить HR/cadence/altitude/power/developer fields;
- CRC/structure validity;
- повторно decode output;
- `warpbuster validate`;
- `warpbuster diff`.

## Diff должен показывать

- records changed;
- fields changed;
- fields unexpectedly changed;
- preservation percentages.

## Andromeda acceptance

- output FIT читается;
- timestamps unchanged;
- trusted position records outside interval unchanged;
- sensors unchanged;
- distance больше не содержит teleport contribution;
- Garmin/Strava manual upload compatibility проверяется вручную вне automated CI.

## Acceptance Criteria

- validation green;
- preservation regression green;
- no unexpected field changes без объяснения;
- все предыдущие tests зелёные.
