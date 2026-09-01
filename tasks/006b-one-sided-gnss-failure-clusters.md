# Task 006B — One-sided GNSS Failure Clusters

Статус: выполнена.

## Контекст

После частичного восстановления `Andromeda_Taras.fit` в output остаётся визуально
ошибочный фрагмент примерно `6.6..7.0 км`. Это не ранее известный skipped mixed region
`8820..9580`, а отдельный residual GNSS failure сразу после основного восстановленного
interval.

Наблюдаемая morphology в текущем private fixture:

- основной HIGH interval заканчивается record `3254` примерно на `6.02 км`;
- residual cluster расположен ориентировочно в records `3626..3700`;
- transition `3626 -> 3627`: `IMPOSSIBLE`, около `68.5 м` за `1 с`;
- transitions `3627 -> 3628`, `3641 -> 3642`, `3642 -> 3643`: `SUSPICIOUS`;
- missing-position runs: `3632..3640` и `3673..3700`;
- отклонение от приватного reference fixed FIT достигает примерно `192 м` около record
  `3672`.

Текущий island detector не создаёт `CorruptedInterval`, потому что morphology не даёт
классическую пару impossible entry/exit transitions. В результате участок отсутствует
и среди reconstruction candidates, и среди явных skipped intervals.

## Цель

Научиться детерминированно и консервативно находить bounded GNSS failure clusters, в
которых impossible/suspicious evidence сочетается с missing-position gaps и поэтому
обычная entry/exit pairing теряет одну из границ.

Если corruption всего интервала доказана независимо от course, разрешить последующей
course reconstruction построить candidate. Если доказательств недостаточно, участок
должен как минимум появляться в console/JSON как отдельный unresolved diagnostic region
с точными причинами отказа.

## Сначала исследовать

- определить, какие records являются последними устойчивыми anchors до и после
  residual cluster;
- проверить direct bridge, elapsed time и локальный NORMAL context без GPX course;
- отделить физически невозможные координаты от plausible subruns внутри gaps;
- установить, можно ли доказать corruption всего bounded interval, не используя
  reference fixed FIT или distance-to-course;
- сравнить несколько вариантов boundary construction на synthetic fixtures.

## Сделать

- добавить vendor-neutral модель и reasons для one-sided/missing-exit GNSS cluster;
- реализовать bounded scan вокруг impossible/suspicious transition и соседних
  missing-position runs;
- проверять устойчивость внешних anchors и физическую достижимость direct bridge;
- не создавать HIGH `CorruptedInterval`, пока не доказано, что все включённые plausible
  coordinates относятся к одному GNSS failure;
- передавать доказанный interval в существующий reconstruction pipeline без отдельной
  Garmin-specific ветки;
- для недоказанного случая формировать явный unresolved interval/region;
- добавить console/JSON diagnostics: boundaries, evidence counts, anchor stability,
  bridge и reasons;
- вынести все новые thresholds и bounds в configuration model;
- добавить synthetic regressions, private Andromeda acceptance и performance check.

## Строгие правила

- GPX course, reference fixed FIT, OSM и DEM не участвуют в corruption detection или
  построении границ detected core;
- приватный `Andromeda_Taras_FIXED.fit` используется только как test oracle;
- один impossible jump сам по себе не доказывает corruption всего соседнего участка;
- stable outer anchors и plausible bridge являются необходимым evidence, но могут быть
  недостаточны для auto-repair;
- physically plausible movement не изменяется без независимого доказательства ошибки;
- timestamps не изменяются;
- unresolved результат предпочтительнее false positive;
- scan должен быть bounded и не ухудшать целевую производительность анализа.

## Acceptance Criteria

- synthetic one-sided cluster с impossible transition и последующим GNSS dropout
  обнаруживается и имеет стабильные boundaries/reasons;
- обычный tunnel/dropout, остановка, switchback и реальный off-course detour не получают
  ложный HIGH corrupted interval;
- residual Andromeda region примерно `3626..3700` больше не остаётся невидимым: он
  присутствует в console/JSON как reconstructable или unresolved;
- auto-repair разрешается только при выполнении явно названного course-independent
  proof rule;
- при repair report перечисляет этот region как `APPLIED` или `SKIPPED` с причиной;
- основной interval `1794..3254` и mixed region `8820..9580` не регрессируют;
- все существующие tests и performance target остаются зелёными.

## Не делать

- подбор границ detected core по близости к GPX course; reconstruction может расширить
  уже доказанный `MEDIUM` core до устойчивого course corridor с явной диагностикой;
- принудительное восстановление всего региона только ради визуально красивой линии;
- использование приватного reference fixed FIT в production pipeline;
- изменение writer policy, timestamps или sensor fields;
- OSM/DEM routing и map matching.

## Реализовано

- `integrity/one_sided.py`: bounded scan по unpaired impossible entries;
- proof rule: missing-terminated cluster, stable outer anchors, plausible bridge и
  abnormal evidence во всех positioned-компонентах;
- `MEDIUM` `one_sided_cluster` либо подробный `LOW` unresolved diagnostic;
- console/JSON/HTML audit: boundaries, anchors, context, bridge, evidence/components,
  confidence и reasons;
- existing course pipeline принимает `MEDIUM` interval, но default HIGH его пропускает;
- MEDIUM-only wider anchor matching, плавные connectors без изменения trusted anchors,
  timestamp fallback и post-check невозможных candidate transitions;
- synthetic false-positive/boundary/performance tests и private Andromeda acceptance.

Для private Andromeda доказанный без course core остаётся `3627..3700`, anchors
`3626/3701`, direct bridge около `187.2 м / 75 с = 2.50 м/с`, два из двух tainted
positioned-components. Reconstruction больше не принимает эти локально плавные, но уже
дрейфующие anchors: bounded scan требует 15 устойчивых records в corridor `15 м` и
расширяет repair scope до `3582..3741`, anchors `3581/3742`. Их расстояния до course
около `12.98/14.91 м`, connectors `27.89 м`, полный reconstruction path `235.73 м`.
Detected core и refined scope оба явно присутствуют в report. Candidate остаётся
`MEDIUM` и применяется только при `--min-confidence medium`. Регион `8820..9580` не
повышен и остаётся skipped.

Дополнительный линейный altitude scan сообщает sensor warnings, не создавая coordinate
interval: на private FIT обнаружены sustained `1608..1633` (`+129 м / 25 с`) и single
extreme `8618..8619` (`-11.8 м / 1 с`). Это не считается самостоятельным доказательством
порчи GNSS coordinates, поэтому writer altitude не меняет.

Известное ограничение: course corridor подтверждает возврат к reference geometry, но не
является detector evidence и не повышает confidence выше `MEDIUM`. OSM routing, DEM и
map matching сознательно не используются. Если stable corridor, matching, ambiguity или
physical post-check не проходят, регион остаётся unresolved.
