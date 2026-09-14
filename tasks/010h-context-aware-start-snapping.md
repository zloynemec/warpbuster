# Task 010H — Привязка начала маршрута по контексту перед разрывом

Статус: реализовано и проверено локально 2026-09-14.
Предыдущие этапы: 010D (snapping), 010E (alternatives), 010G (trace audit).
Интеграция: Core OSM discovery из 012A, включая совместный сбор GPX/OSM-кандидатов.

## 1. Проблема и цель

Сейчас начальная опора маршрута привязывается к OSM по одной координате. Если две
неэквивалентные группы дорог имеют близкие расстояния, возвращается AMBIGUOUS_SNAP,
хотя последовательность исходных точек перед разрывом может поддерживать одну дорогу.

Цель: разрешать такую неоднозначность только при достаточном, согласованном контексте
движения перед разрывом. Это выбор начальной привязки; он не доказывает весь маршрут
внутри пропуска и не разрешает автоматическое восстановление FIT.

## 2. Мотивирующий пример: Andromeda G2

Начальная опора — record 1793, gap — records 1794–3254:

- OSM way 1383524397: 17.510 м от опоры;
- OSM way 97845490: 26.865 м от опоры;
- разница 9.355 м при пороге неоднозначности 10 м: AMBIGUOUS_SNAP;
- конечная опора принята; маршрут пока не строится.

Локальная диагностика показывает:

| Исходные точки | Медиана расстояния до первой дороги | До второй |
| --- | ---: | ---: |
| 1752–1767 | 1.64 м | 58.62 м |
| 1774–1793 | 16.99 м | 30.16 м |

Между этими окнами есть G1. Сильное совпадение первого окна не разрешает переносить
его вывод через пропуск. В первом срезе для G2 допустим только непрерывный suffix,
заканчивающийся record 1793; максимум 1774–1793, если все точки проходят eligibility.
GPX также близок к первой дороге, но не используется как голос в данном алгоритме.
Нельзя подбирать thresholds специально ради успешного результата Andromeda.

## 3. Границы задачи

Реализовать:

1. Сбор ограниченного контекста исходного FIT в Core после Integrity Detector.
2. Необязательный typed start context в routing API и Core adapter.
3. Разрешение только начального AMBIGUOUS_SNAP по последовательности точек.
4. Общую реализацию для single-route и alternatives, audit и diagnostics.
5. Представление причин выбора/отказа в JSON и HTML, synthetic и private проверки.

Не реализовывать: классификацию GNSS по OSM/GPX; изменение detector, mask, границ gap,
координат и timestamps; выбор между GPX и OSM; allocation/application; уточнение
конечной опоры; сбор контекста через пропуски; поиск далёких дорог; общий map matching;
автоматическую замену исходной опоры предыдущей точкой; web deployment.

## 4. Контракт контекста и eligibility

Core передаёт неизменяемую последовательность record index, timestamp и исходных
WGS84-координат в хронологическом порядке, включая начальную опору маршрута.
Routing не импортирует Core и не принимает GPX или восстановленные координаты.
Core отвечает за eligibility; routing повторно валидирует типы, порядок, конечность,
длительность, количество и совпадение последней координаты с request.start.

Сканировать назад от anchor_before; принимать только PRESERVED + anchor_eligible
из независимой coordinate mask, в том же continuity segment. Остановиться при:

- missing/invalidated/неподходящей точке, смене continuity;
- нестрого возрастающем времени, pause/event boundary, чрезмерном временном шаге;
- достижении временного/пространственного/численного лимита.

Не перепрыгивать неподходящие точки и не заменять их интерполяцией. Не выдавать
PRESERVED за доказательство точности GPS. Для пригодности контекста нужны измеримые
длительность и продвижение, а не только большое число записей стоящего человека.
Недостаточный контекст — обычный диагностируемый отказ, а не повод ослабить проверки.

Существующие callers без context сохраняют прежние результаты. Некорректный явно
переданный context — controlled input error; отсутствующий/недостаточный — прежний
AMBIGUOUS_SNAP с причиной. Новые поля additive, default отсутствует.

## 5. Алгоритм разрешения неоднозначности

1. Выполнить прежнюю проверку опоры: coverage, profile/access, radius, distance,
   группировку эквивалентных directed edges. ACCEPTED/NO_SNAP/OUTSIDE_COVERAGE не менять.
2. Только для AMBIGUOUS_SNAP сравнить все допустимые группы в полосе неоднозначности
   относительно ближайшей. Не выбирать subset по порядку ответа или обрезанному UI.
   Если группа конкурентов превышает ресурсный лимит — сохранить отказ.
3. Получить bounded geometry/metadata этих групп из того же verified graph.
   В первом срезе проверять только представленные локальные graph edges: не расширять
   граф поиском предшественников. Если контекст требует такого расширения — отказ.
4. Спроецировать каждую точку на каждую допустимую ориентированную геометрию. Проверить
   абсолютные ошибки, устойчивость преимущества по последовательности, продвижение
   по chainage и согласованность порядка. Самопересечения, несколько сопоставимых
   проекций или смена поддерживаемой дороги к концу контекста не разрешать ранжированием.
5. Выбрать группу только при выполнении всех абсолютных gates и достаточном преимуществе
   над каждым конкурентом. Одной минимальной средней ошибки недостаточно. Проверять
   также недавнюю часть контекста: старые точки не должны подавлять свидетельство поворота.
6. Направление оценивать отдельно от identity дороги. Противоположные directed IDs
   одной дороги не считать двумя дорогами. При недоказанном направлении, важном для
   привязки/продолжения маршрута, сохранить отказ; арифметику IDs не использовать.
7. Продолжить обычный routing с выбранной привязкой исходной опоры. Контекст не
   подменяет endpoint, радиус или max snap distance. Проверить, что native routing
   действительно использовал принятую группу, а не повторно выбрал соседнюю дорогу.
8. Применить все прежние route/trace/geometry/access/version проверки, включая 010G.
   Сбой любого audit не превращать в частичный успех и не обходить другим snap.

GPX, длительность/длина полного маршрута через пропуск и положение конечной опоры
не участвуют в выборе стартовой дороги этого этапа. Нет гарантии нахождения
правильного полного пути; alternatives остаются non-exhaustive.

## 6. Конфигурация, сложность и детерминизм

Все thresholds имеют имя, единицы, default, объяснение и boundary tests.
До написания алгоритма согласовать в implementation plan таблицу defaults на основе
синтетических сценариев; значения из одного приватного трека не являются калибровкой.
Обязательные параметры:

- enable/disable контекстного разрешения;
- максимальные число точек, возраст контекста (с), длина контекста (м), шаг времени (с);
- минимальные число точек, длительность (с), продвижение (м);
- максимальные median/верхний quantile ошибки (м), допустимое обратное продвижение (м);
- минимальное преимущество над конкурентом (м) и доля согласованных точек;
- размер недавнего проверочного окна (с/точки) и его критерии согласованности;
- максимальные группы, vertices, metadata calls, decoded bytes и общий work budget.

Отдельно зафиксировать формулу quantile, правила ties и порядка результатов. Равенство
порогов консервативно трактовать как отказ, если это не противоречит существующему gate.
Тип bool вместо числа, NaN/Inf и недопустимые сочетания конфигурации отклонять.

Сложность ограничена context_points × total_candidate_vertices. Нет полного O(n²)
по активности и locate-запроса на каждый record. Metadata calls ограничены именованным
бюджетом; одна оценка начальной привязки на весь запрос alternatives. Неудачные попытки
тоже расходуют лимиты; новые budgets не обходят существующие operation limits 010G.
Policy/config влияют на provenance; граф не пересобирается из-за request policy.
Идентичная принятая geometry/traversal сохраняет прежний route ID.

## 7. Отчёт и ошибки

Сохранить исходное single-point решение и добавить context audit:

- policy version, параметры, диапазон records, количество/длительность контекста,
  причина остановки его сбора, hash входного контекста;
- ID сравниваемых групп/ways/directed edges и доказательства направления;
- абсолютные ошибки, progression, support fraction, последние точки и margin;
- итог: resolved / insufficient_context / conflicting_context / ambiguous_projection /
  insufficient_margin / unsupported_geometry / resource_limit;
- выбранная группа или явное отсутствие выбора.

Имена новых кодов согласовать с существующим контрактом ошибок. Подлинное исчерпание
operation budget и ошибки backend/cache не маскировать обычной неоднозначностью.
В HTML для G2 объяснять отдельно: привязка разрешена/отклонена, маршрут найден/не найден,
кандидат применён/не применён. Не маркировать context-resolved как HIGH confidence
восстановления и не скрывать конкурирующие дороги.

## 8. Ожидаемые файлы

- Core: reconstruction/osm.py, конфигурация и необходимые additive модели;
- Routing: models.py, snapping.py, route_service.py, config.py; при необходимости
  отдельный модуль context snapping;
- соответствующие provider/API/snapping/native tests;
- report/osm.py и report/assets/report.html для additive диагностики;
- package README, пример TOML, документация 012A.

Перед кодом уточнить реальные пути, минимальный typed contract, формулы и defaults.
Не включать unrelated незакоммиченные изменения в эту задачу.

## 9. Тесты и acceptance criteria

1. Без context либо при feature disabled — прежние snap outcomes, geometry и IDs.
2. Synthetic native graph с двумя соседними дорогами: одна опора неоднозначна,
   непрерывный контекст устойчиво поддерживает одну; оба API выбирают правильную
   группу, native route следует ей и проходит полный audit.
3. Обратное направление и перестановка backend candidates дают корректный,
   детерминированный результат. Эквивалентные opposite edges не создают ложную конкуренцию.
4. Отказы: равноценные параллельные дороги, стоянка, малое продвижение, короткий context,
   свежий поворот/смена поддержки, loop/crossing, missing metadata, недоказанное направление.
5. Точки через missing/invalidated/pause/continuity boundary не используются. Для схемы
   «сильный старый контекст → пропуск → слабый новый» старый контекст не разрешает snap.
6. Запрещённый access, endpoint вне покрытия или max distance не исправляются контекстом.
   Повторная native корреляция к другой дороге обнаруживается и отклоняется.
7. Все thresholds, malformed input и ресурсные ограничения имеют boundary tests;
   повторный вызов даёт тот же canonical audit. Никаких скрытых retries/reroutes.
8. GPX-кандидаты, detector mask, timestamps, selection и FIT write scope не меняются.
   При наличии GPX OSM discovery по-прежнему выполняется.
9. Private Andromeda: повторить G2 на том же graph; зафиксировать eligibility каждой
   точки suffix 1774–1793 и результат сравнения. Если evidence недостаточен — объяснимый
   AMBIGUOUS_SNAP считается корректным; форсированный READY не является критерием.
   При resolution отдельно доказать правильную стартовую группу, прохождение route audit
   и отсутствие автоматического применения. Сохранить JSON/HTML и baseline comparison.
10. Повторить пять приватных пар последнего прогона; проверить SHA-256 всех источников,
    сохранить артефакты в ignored tests/private. Не создавать исправленные FIT.
11. Core + Routing + Manager pytest, Ruff, mypy затронутых packages и diff check проходят.
    Native fixture обязательна; публичные тесты не содержат приватных координат/IDs.

Задача завершена, когда контекст может обоснованно разрешать synthetic ambiguity,
консервативные отказы доказаны тестами, а private результат воспроизводим и объясним.


## 10. Реализация и проверка — 2026-09-14

Добавлены `OSMStartContext`/collector в Core и необязательные typed `StartContext` /
`StartContextPoint` в Routing. Core останавливается на первом неподходящем record,
любом source event, разрыве времени/continuity либо лимите; берёт исходные координаты.
GPX не передаётся в resolver. Legacy reason/code paths для отсутствующего или
отключённого context сохранены. Core collection limits сериализуются в JSON.

Resolver переиспользует original start locate без дополнительных native calls.
Он сравнивает локальные geometry всех групп в ambiguity band, проверяет направление,
chainage/backtrack, ошибки и преимущество над каждым конкурентом. Конкурирующие
локальные минимумы проекции дают отказ. При проверке локальных минимумов обычная
clamped-проекция на соседнюю вершину не считается отдельным минимумом, если соседний
сегмент подходит ближе. Первое directed edge маршрута после полного trace audit
обязательно совпадает с принятым; несовпадение — `context_start_edge`, без reroute.

Defaults зафиксированы в Routing config и example TOML:

| Параметр | Default |
| --- | --- |
| Maximum context | 60 records / 60 s / 150 m; step <= 5 s |
| Minimum context | 8 records / 7 s |
| Minimum progression | > 10 m |
| Median / P90 error | < 20 m / < 25 m |
| Total backward progression | < 3 m |
| Advantage over each rival | > 10 m on >= 80% of points |
| Recent window | 5 points; same support threshold; every error < 25 m |
| Projection ambiguity | competing local minima within 0.25 m error |
| Maximum comparison work | 8 groups / 4096 unique-shape vertices / 245760 projections over segments |

P90 — nearest-rank `ceil(0.9*N)-1`; median — стандартная медиана. Минимальное
количество/длительность и доля поддержки включают границу: 80% допускает одну
неподдерживающую точку в окне из пяти. Error/backtrack limits и margin/progress —
строгие неравенства. Эти правила проверяются синтетическими сценариями, а не
подгоняются под Andromeda. Decode points, edges и bytes новой работы включены в
существующий operation budget, включая декодирование metadata при нормализации.

В HTML добавлена колонка `Start context evidence` с раскрываемыми evidence, config,
диапазоном records и причинами отказа; route availability и application показаны
отдельно. JSON переносит audit без потери полей. Проверен синтаксис JavaScript через
`node --check`, распарсен embedded JSON всех десяти приватных HTML (enabled/disabled).

### Приватная приёмка

Каждая из пяти пар проверена на одном и том же графе дважды: context disabled/enabled.
Во всех случаях значения candidate count совпали:

| Трек | Disabled | Enabled |
| --- | ---: | ---: |
| Andromeda | 1 | 1 |
| Balaklava | 3 | 3 |
| CHR KayaBayu 22 | 2 | 2 |
| m87 home run | 0 | 0 |
| BST2025 TezBair 55 | 5 | 5 |

Числа для CHR/BST учитывают отдельно реализованный совместный сбор GPX/OSM; они не
являются приростом от 010H. Для m87 exit 3 соответствует отсутствию выбранных изменений.

Andromeda G2: допустимый suffix ровно 1774–1793; G1 ограничивает сбор. Контекст не
разрешил привязку: `ambiguous_projection` на way 1383524397 для records 1786–1788.
Этот консервативный отказ соответствует критерию 9. Сравнение только медиан было бы
недостаточным: близость к дороге не доказывает однозначное ordered matching.

SHA-256 десяти FIT/GPX неизменны. GPX interval plans, selection, gap inventory,
coordinate dispositions и coverage совпали между enabled/disabled. FIT не записывался.
Ignored artifacts: `tests/private/osm-dry-run-20260914/010h-final/`: run.py, runs.json,
enabled/disabled JSON/HTML/logs, andromeda-eligibility.json.

### Ограничения

Поддерживаются только локальные edge geometry из исходного locate. Junction groups,
недостаточная metadata, неоднозначные projections и противоречивый context сохраняют
отказ. Native routing может повторно выбрать другую дорогу; это обнаруживается и
отклоняется, а не исправляется снижением snap thresholds или изменением endpoint.
Стартовое направление не означает уверенность в пути внутри gap. OSM не применяется
автоматически, detector и FIT writer не изменены, deployment не выполнялся.

### Команды проверки

Из корня с dev Python 3.14 и Valhalla 3.8.3:

```bash
PYTHONPATH=src:packages/osm-routing/src:packages/osm-manager/src python -m pytest -q
(cd packages/osm-routing && PYTHONPATH=src:../osm-manager/src python -m pytest -q)
(cd packages/osm-manager && PYTHONPATH=src python -m pytest -q)
python -m ruff check src tests packages/osm-routing/src packages/osm-routing/tests
PYTHONPATH=src:packages/osm-routing/src:packages/osm-manager/src python -m mypy src/warpbuster
(cd packages/osm-routing && PYTHONPATH=src:../osm-manager/src python -m mypy src)
git diff --check
```


Результат release gate: **Core 571 passed, 17 skipped; Routing 438 passed;
Manager 56 passed**. Пропущены только отсутствующие приватные Core fixtures;
новые native tests выполнены. Добавлены 30 Core и 98 Routing тестовых случаев.
Ruff, mypy (Core 51 файл, Routing 21 файл), JavaScript syntax check и diff check
успешны. Acceptance criteria выполнены, включая предусмотренный консервативный
отказ на Andromeda G2. Исходники и артефакты не публиковались.
