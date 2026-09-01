# Task 007 — FIT Writer, Preservation, Validate, Diff

Статус: реализовано.

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

Текущий `Andromeda_Taras.fit` имеет второй unresolved mixed GNSS region `8820..9580`.
Writer с default threshold `HIGH` применяет основной interval `1794..3254`, оставляет
unresolved region без изменения coordinates и явно показывает оба решения в report.

## Acceptance Criteria

- validation green;
- preservation regression green;
- no unexpected field changes без объяснения;
- все предыдущие tests зелёные.

## Реализованная write strategy

- исходные FIT definitions и data frames сохраняются в исходном порядке и размере;
- patch меняет bytes только у явно разрешённых scalar fields;
- footer CRC пересчитывается, затем temporary output повторно декодируется;
- публикация выполняется атомарно и без implicit overwrite; явный `--overwrite`
  разрешает замену generated output, но не source FIT;
- `record.distance` корректируется заменой increments на edges, затронутых новыми
  coordinates; correction переносится на последующие cumulative values;
- `lap/session.total_distance` и существующие average-speed summaries пересчитываются;
- сломанный summary `timestamp` не искажает correction: граница берётся из
  `start_time + total_elapsed_time`, если эти поля доступны и согласованы с records;
- исходные record/lap/session timestamps не изменяются;
- каждый available interval candidate выбирается по `--min-confidence` (`LOW`, `MEDIUM`,
  `HIGH`; default `HIGH`), partial application разрешено;
- report перечисляет все detected intervals как `APPLIED`/`SKIPPED` с confidence,
  candidate availability, update count и reasons;
- record speed сохраняется: его producer может быть footpod/Stryd/device fusion, и
  зависимость от GNSS coordinates без дополнительного evidence не предполагается.

## Не реализовано сознательно

- добавление отсутствующих FIT fields через изменение definitions;
- переписывание FIT через GPX или полную profile-based re-encoding;
- изменение max speed и полей с неизвестной FIT semantics;
- automated Garmin Connect/Strava upload.
