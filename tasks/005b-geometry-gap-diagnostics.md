# Task 005B — Geometry Gap Diagnostics

## Цель

Находить длинные, плотно дискретизированные и почти идеально прямые участки, которые
могли появиться из-за интерполяции между двумя GNSS-точками, даже когда timestamps
отсутствуют и соседние переходы выглядят правдоподобно.

## Сделать

- отдельный geometry-only diagnostic pass после physical detector;
- warning `possible_interpolated_gnss_gap` с границами и измеримой геометрией;
- метрики chord distance, sampled path distance, path/chord ratio и максимального
  поперечного отклонения;
- `LOW` confidence и явный `repair_eligible=false`;
- bounded scan и ограничение количества сохраняемых warnings;
- console, JSON и `-vv` diagnostics;
- synthetic false-positive, continuity, retention и performance regressions;
- private acceptance для `Orion_Artyom.gpx`, если файл доступен локально.

## Строгие правила

- warning не меняет итоговый `CLEAN`, `UNKNOWN`, `SUSPICIOUS` или `CORRUPTED` status;
- warning не создаёт `CorruptedInterval` и не разрешает repair;
- отсутствие timestamps не превращает геометрическую эвристику в доказательство;
- course, OSM, DEM и внешние сервисы не используются;
- реальные прямые остаются допустимы: false positive безопаснее представить как
  advisory warning, чем как corruption;
- все thresholds и bounds находятся в `IntegrityConfig`.

## Acceptance Criteria

- синтетический длинный идеальный chord создаёт один `LOW` warning;
- итоговый status и exit code остаются такими, какими их определил physical detector;
- реалистично шумная прямая, плавная кривая и разные continuity segments не создают
  ложный warning;
- JSON содержит стабильные метрики, reasons и `repair_eligible=false`;
- количество сохранённых warnings и объём scan ограничены конфигурацией;
- анализ 20 000 records остаётся быстрее 5 секунд на современном ноутбуке;
- существующие FIT/GPX tests остаются зелёными;
- локальный `Orion_Artyom.gpx` показывает известный искусственно прямой участок без
  объявления файла corrupted.

## Не делать

- attribution к Garmin, COROS, Strava или конкретному алгоритму интерполяции;
- course matching;
- reconstruction/repair;
- изменение timestamps или coordinates;
- автоматическое повышение geometry warning до corruption.
