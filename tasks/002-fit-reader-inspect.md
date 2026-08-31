# Task 002 — FIT Reader + Inspect

## Цель

Прочитать реальный FIT и преобразовать его в vendor-neutral `ActivityData`.

## Сделать

- выбрать FIT decoding strategy/library и кратко документировать выбор;
- reader adapter;
- normalized ActivityData / ActivityRecord;
- сохранить ссылку/metadata, необходимую будущему lossless patching;
- `warpbuster inspect activity.fit`;
- console summary;
- по возможности `inspect --json`;
- тесты reader-а и normalization.

## Inspect должен показывать

- start/duration;
- record count;
- recorded distance;
- coordinate bounds;
- какие sensor fields присутствуют;
- laps/sessions/events count;
- developer/unknown field summary, если доступно.

## Важно

Не выбрасывать неизвестные FIT messages/fields без необходимости.

## Не делать

- anomaly detection;
- repair;
- GPX;
- FIT writing.

## Acceptance Criteria

- реальный Garmin FIT читается;
- private Andromeda FIT читается;
- records имеют timestamps/coordinates там, где они есть;
- missing coordinates представлены как `None`, а не как `(0,0)`;
- inspect не падает на developer fields;
- тесты зелёные.
