# Task 005C — Coordinate / Odometer Consistency Diagnostics

## Статус

Запланирована, но заблокирована до появления подходящего приватного исходного FIT.
Реализацию не начинать по скриншоту или GPX export: сначала нужно исследовать реальные
`record.distance`, `session.total_distance`, доступные sensor/developer fields и их
поведение на повреждённом GNSS-интервале.

## Цель

Находить интервалы, где геометрическая дистанция GNSS-координат резко расходится с
накопленной дистанцией, записанной устройством или внешним датчиком, не считая один
только odometer доказательством повреждения координат.

Типовой сценарий: GNSS рисует физически невозможный уход и возврат, но Garmin хранит
правдоподобную дистанцию, которая могла поступать от footpod/Stryd или другого
независимого источника.

## Обязательный prerequisite

Получить приватный оригинальный FIT с похожей ошибкой и сохранить его только в
`tests/private/`. До проектирования thresholds документировать:

- какие distance/speed fields реально присутствуют в records и session;
- монотонна ли recorded distance;
- меняется ли её источник или поведение на паузах;
- согласуется ли итоговая FIT distance с показанием Garmin/Strava;
- есть ли явные metadata, позволяющие установить источник distance.

Наличие Stryd рядом со спортсменом само по себе не доказывает, что Garmin использовал
его для distance/speed. Неизвестное происхождение поля нужно так и обозначать.

## Сделать после получения fixture

- определить vendor-neutral модель recorded odometer evidence;
- сравнивать прирост recorded distance с geodesic path distance только внутри одного
  continuity domain;
- использовать bounded windows, устойчивые к обычному GNSS noise и разной sampling
  cadence;
- распознавать unusable evidence: missing distance, reset, decrease, duplicate values,
  pause и неизвестную семантику;
- формировать отдельный machine-readable warning о сильном расхождении;
- показывать recorded/geodesic deltas, ratio, границы и качество evidence в console,
  JSON и `-vv` diagnostics;
- добавить все thresholds, tolerances и scan bounds в `IntegrityConfig`;
- добавить synthetic tests, private FIT acceptance и performance regression.

## Строгие правила

- recorded distance не является автоматически trustworthy;
- расхождение odometer/GNSS само по себе не создаёт `CorruptedInterval` и не повышает
  status до `CORRUPTED / HIGH`;
- odometer evidence можно прикреплять как corroborating reason только к corruption,
  уже установленному независимыми физическими признаками;
- detector не должен угадывать Garmin/Stryd/COROS semantics или источник поля;
- FIT timestamps, coordinates, distance и sensor data не изменяются;
- course, OSM, DEM и внешние сервисы не используются;
- GPX без recorded distance не получает синтетическую odometer stream;
- core остаётся vendor-neutral, deterministic, offline и bounded по сложности.

## Acceptance Criteria

- приватный FIT сначала исследован, а semantics используемых полей документированы без
  неподтверждённой attribution;
- известный GNSS incident показывает измеримое расхождение coordinate/odometer distance;
- warning не меняет status и repair eligibility самостоятельно;
- clean run, trail switchbacks, stop/restart, GNSS noise, pause и нормальная разница
  sensor/GPS distance не создают сильный warning;
- missing, decreasing или reset distance классифицируется как unusable/unknown evidence;
- существующие FIT/GPX detection results остаются неизменными;
- анализ 20 000 records остаётся быстрее 5 секунд на современном ноутбуке;
- все tests, lint, format и type-check проходят.

## Не делать

- реализацию до получения подходящего приватного FIT;
- определение источника distance по названию устройства или скриншоту;
- reconstruction/repair;
- пересчёт или замену recorded distance;
- импорт proprietary Stryd/Garmin API;
- использование GPX course как detector evidence.
