# Architecture Decisions

## ADR-001 — Python

Core и CLI пишутся на Python 3.14+ из-за сильной экосистемы numerical/GIS/trajectory processing.

Статус: Accepted.

## ADR-002 — FIT-first

FIT — главный input/output формат. GPX — course/reference/export, но не промежуточный canonical representation.

Статус: Accepted.

## ADR-003 — Detector не знает course

Integrity Detector не использует GPX/OSM/course distance.

Причина: настоящий wrong turn на трейле не является GPS corruption.

Статус: Accepted.

## ADR-004 — Physical plausibility first

Основание для corruption — нарушение физической непрерывности, а не «красивость» трека.

Статус: Accepted.

## ADR-005 — Timestamps immutable during GNSS repair

Невозможная GPS-скорость не исправляется растягиванием времени.

Статус: Accepted.

## ADR-006 — Long spoofing islands are first-class

Detector должен анализировать интервалы, а не только локальные spikes.

Статус: Accepted.

## ADR-007 — Reconstruction separate from detection

Detection выдаёт corrupted interval; reconstruction отдельно выбирает способ восстановления.

Статус: Accepted.

## ADR-008 — Conservative auto-repair

LOW/MEDIUM confidence не должен автоматически менять FIT.

Статус: Accepted.

## ADR-009 — No AI in core

Алгоритм deterministic/offline.

Статус: Accepted.

## ADR-010 — OSM/routing postponed

Map-based reconstruction не входит в v0.1.

Статус: Accepted.

## ADR-011 — Private user fixtures

Реальные FIT пользователя не коммитить в public repo по умолчанию.

Статус: Accepted.

## ADR-012 — fitdecode for frame-preserving FIT decoding

FIT decoding в v0.1 выполняется через пакет `fitdecode`.

Причины:
- поддержка FIT protocol v2, developer fields и compressed timestamp headers;
- CRC validation;
- последовательный доступ к header/definition/data/CRC frames;
- сохранение offset и исходных bytes каждого frame;
- неизвестные messages/fields не требуют преобразования через GPX или другую модель.

Официальный `garmin-fit-sdk` используется в dev dependencies для генерации synthetic
fixtures, но не как основной reader: текущий Python decoder не поддерживает compressed
timestamp headers и не предоставляет столь же удобную 1:1 frame representation.

Normalized model остаётся vendor-neutral и не импортирует decoder. Reader сохраняет
исходные FIT bytes, порядок decoded messages, raw definition/data chunks и ссылку каждого
`ActivityRecord` на исходный record. Стратегия записи/patching выбирается отдельно в
Task 007; decoded objects не считаются lossless canonical representation.

Статус: Accepted.

## ADR-013 — Conservative local transition thresholds

Task 003 классифицирует только переходы между соседними records с доступной позицией.
Records без позиции учитываются отдельно и не считаются teleport; при переходе через
такой gap используется полный elapsed time исходных timestamps.
Если более сильной аномалии нет, наличие missing position или невычислимого `dt`
понижает итоговый status до `UNKNOWN`, а не создаёт ложный `CLEAN`.

Evidence разделён на два уровня:

- абсолютный предел одновременно по apparent speed и длине перехода может дать
  `IMPOSSIBLE` и HIGH confidence;
- robust-relative outlier относительно median/MAD может дать только `SUSPICIOUS` и
  LOW confidence.

Абсолютная физическая граница зависит от вида активности. Reader нормализует `sport`
и `sub_sport`, после чего detector выбирает именованный profile:

- `running`: `25 m/s` вместе с дистанцией не менее `50 m` для `IMPOSSIBLE`;
- `generic`: absolute ceiling отключён, поэтому скорость сама по себе может дать только
  `SUSPICIOUS / LOW`.

Running ceiling более чем вдвое выше средней скорости мирового рекорда 100 m
(`100 / 9.58 ≈ 10.44 m/s`) и дополнен distance floor. Источник результата:
<https://worldathletics.org/records/all-time-toplists/sprints/100-metres/all/men/senior>.

Relative floor остаётся `20 m/s` вместе с дистанцией не менее `20 m`. При наличии пяти
samples он дополнительно повышается до максимума из floor, `6 × median` и
`median + 10 × MAD`. Все значения находятся в `IntegrityConfig`, сериализуются в JSON
report и могут быть переопределены явной конфигурацией.

Course, recorded FIT speed и distance не используются как доказательство повреждения.
Task 004 отдельно решит, какие локальные transitions ограничивают единый corrupted
interval.

Статус: Accepted.
