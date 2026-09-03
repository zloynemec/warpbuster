# Task 010E — Alternatives + Route Diagnostics

Статус: завершена 2026-09-03; ТЗ согласовано перед реализацией.

Предыдущая задача: [010D — Audited Snapping + Single-route API](010d-audited-snapping-single-route.md).

## Простыми словами

Сейчас мы умеем привязать две координаты к локальному OSM graph и получить один
проверенный маршрут между ними. Но между теми же точками могут проходить разные тропы.
Предпочтительный для routing engine путь не обязательно совпадает с фактически
пройденным человеком.

В 010E нужно получить небольшой набор вариантов и ответить:

- сколько разных маршрутов вернул движок;
- насколько они длиннее или короче основного;
- насколько совпадает их движение по graph edges;
- какие warnings есть у каждого варианта;
- есть ли несколько вариантов, между которыми routing сам выбирать не должен.

Задача **не выбирает маршрут для исправления тренировки и не меняет FIT**. Она готовит
проверяемые данные для будущего Task 011.

## 1. Цель и границы

Добавить opt-in bounded alternatives поверх существующих graph cache, trail profile,
audited snapping и per-route audit из 010B–010D.

Обязательно:

- только локальный verified graph по точному `graph_id`;
- ровно два WGS84 anchors, один неизменённый `warpbuster-trail-running-v1` profile;
- один запрос route к Valhalla, без собственного поиска путей;
- независимый audit каждого возвращённого маршрута;
- стабильные IDs, порядок и попарные метрики;
- явные ограничения полноты поиска и отсутствие автоматического выбора для repair;
- сохранение существующего single-route API и CLI.

Запрещено расширять задачу на FIT, Integrity Detector, timestamps/pace спортсмена,
course matching, DEM, network acquisition или изменение OSM Manager.

## 2. Что подтверждено и что ещё проверить

[Официальная документация Valhalla](https://valhalla.github.io/valhalla/api/route/api-reference/)
описывает `alternates` как число дополнительных маршрутов; движок может вернуть меньше
запрошенного числа, включая ноль. Это не обещание полного перечисления путей.

В локальном runtime Valhalla 3.8.3 проверено:

- `service_limits.max_alternates = 2`;
- spike 010A уже читает основной `trip` и дополнительные `alternates[].trip`;
- production service 010D явно посылает `alternates=0` и проверяет один `trip`.

Поэтому предлагаемый предел 010E — **основной маршрут плюс максимум два дополнительных**.
Это engineering bound текущей интеграции, а не число всех возможных путей в OSM.

В начале реализации обязательна offline проверка на synthetic graph с двумя обходами:
Valhalla 3.8.3 действительно возвращает pedestrian alternative, а каждый вариант
проходит `trace_attributes(shape_match=edge_walk)` с текущим profile. Проверить также
семантику shape indices, direction IDs и partial first/last edges. Если этот probe
не удаётся, сначала документировать ограничение и пересогласовать объём; не заменять
его собственным pathfinding или ослаблением profile. Современная upstream-документация
не заменяет behavioral test используемого runtime.

## 3. Пользовательский контракт

Поддерживаемые вызовы:

```bash
# Существующее поведение: один маршрут, прежний JSON contract.
.venv/bin/warpbuster-osm-route route sha256:GRAPH_DIGEST \
  --from 44.540049,33.690680 \
  --to 44.528600,33.725055 \
  --json

# Основной маршрут и до двух дополнительных; число 2 не включает основной.
.venv/bin/warpbuster-osm-route route sha256:GRAPH_DIGEST \
  --from 44.540049,33.690680 \
  --to 44.528600,33.725055 \
  --alternates 2 \
  --json > routes.json
```

- `--alternates` по умолчанию `0`; поддерживаются `0`, `1`, `2` в пределах config;
- `0` делегирует прежнему single-route API, не запускает поиск alternatives;
- `1`/`2` включает новый ответ с набором маршрутов;
- `--config` и `--cache-dir` работают как сейчас;
- stdout в JSON-режиме содержит ровно один JSON document; diagnostics идут в stderr;
- неверное число не округляется и не обрезается молча: controlled error, exit 2;
- thresholds не выносятся в дополнительные CLI flags: они принадлежат TOML/config;
- GPX/HTML export нескольких маршрутов в 010E не добавляется.

Typed Python boundary:

```text
RouteService.route(RouteRequest) -> RouteResult                       # без breaking changes
RouteService.alternatives(RouteAlternativesRequest) -> RouteAlternativesResult
```

Новый immutable request содержит `graph_id`, `start`, `end`, `alternates` (1 или 2).
Новый result предоставляет typed candidates и их coordinates, а также `as_dict()`
с defensive copy; consumer не должен разбирать raw Valhalla JSON.

## 4. Совместимость и provenance

Существующие `operation=route`, `protocol_version=1`, `route`, `coordinates`, statuses
и exit codes сохраняются. Для нового ответа вводится отдельная операция
`operation=route_alternatives`, `protocol_version=1`; совместимость определяется парой
`operation + protocol_version`, а не одним номером. Старый `route` object не превращается
в массив под прежним именем.

Новый ответ сохраняет request, graph/snapshot/runtime/profile provenance, coverage,
query policy и оба snapping decisions из 010D. Дополнительно публикуются
`alternatives_policy`, её version/hash и effective requested count.

Alternatives policy — request-time настройка. Она не меняет `graph_id`, manifest v2
или build profile и не требует rebuild существующих current graphs. Она не должна
попасть в build cache identity через общий список limits. Legacy graph behavior из
010D остаётся прежним.

## 5. Pipeline и audit

1. Проверить request/config и ровно один раз загрузить verified graph.
2. Выполнить независимый snapping обоих anchors по policy 010D.
3. При negative snap outcome вернуть прежнюю причину; route не запускать.
4. Сделать один `Actor.route` с `alternates=N`, ровно двумя break locations и тем же
   trail profile. Не использовать time-dependent routing.
5. Проверить структуру ответа и counts до обхода shape/edge arrays.
6. Проверить основной `trip` и каждый `alternates[].trip` по требованиям 010D:
   endpoints относительно тех же audited snaps, length, shape, ordered edge provenance,
   pedestrian attributes, restrictions и bounds.
7. Выполнить дополнительные проверки, необходимые для метрик: все geometry segments
   однозначно покрыты audited edge spans; нет незамеченных пропусков/двойного учёта;
   IDs, длины и indices имеют корректные типы и конечные значения. Полная проверка
   shape span нужна и основному маршруту нового ответа.
8. Удалить только точные дубликаты, присвоить стабильные IDs, упорядочить alternatives,
   вычислить метрики и ambiguity diagnostics.

Нельзя использовать `map_snap` вместо `edge_walk`, увеличивать snap radius ради
дополнительного пути, повторять запросы с другими costing options, исключать edges или
перебирать новые anchors. Возможность построить route не разрешает `AMBIGUOUS_SNAP`.

Если **любой** возвращённый engine candidate нарушил обязательный audit, весь новый
query получает `ERROR / ROUTE_AUDIT_FAILED`, без publishable `routes`. Нельзя молча
отбросить сломанный alternative и объявить оставшийся путь единственным найденным.
Диагностика ошибки указывает engine slot (`primary` или индекс alternative), check и
ограниченные подробности. Resource/engine failures также не маскируются под отсутствие
alternatives. Это policy целостности routing response, не изменение partial FIT repair.

Все existing warnings (`FERRY_USED`, `DESTINATION_ONLY_SNAP`) сохраняются по каждому
маршруту. Warning не является основанием скрыть alternative или выбрать другой за Core.

## 6. Идентичность, дубликаты и порядок

- `route_id` — SHA-256 versioned canonical payload: graph ID, request-profile hash,
  polyline6 geometry и ordered directed edge IDs с shape spans.
- ID не зависит от номера alternative, времени выполнения и порядка engine response.
- Геометрия не упрощается и не округляется дополнительно перед audit/deduplication.
- Дубликат — совпадение canonical geometry **и** ordered directed traversal; совпадения
  OSM way IDs или большого overlap недостаточно.
- Одинаковая картинка с разными edge IDs не объединяется; выдаётся diagnostic
  `COINCIDENT_GEOMETRY_DIFFERENT_EDGES`.
- Основной `trip` сохраняется первым, с `role=primary`; это выбор engine, не рекомендация
  для восстановления и не гарантия кратчайшего пути.
- Остальные unique routes сортируются по `(calculated_length_m, route_id)` с
  зафиксированной миллиметровой точностью сортировки; это presentation order.
- Дубликат основного объединяется с основным; removed slots и `duplicate_of` аудируются.
- Противоречащие audit/summary данные у exact duplicate дают controlled
  `ROUTE_AUDIT_FAILED / duplicate_consistency`, а не выбор случайной версии данных.
- Raw engine order при необходимости сохраняется в отдельном diagnostic блоке и не
  участвует в semantic identity результата.

Повторяемость гарантируется для одинаковых snapshot, engine version, profile, request
и query policies. Между разными graph/runtime versions стабильность edge IDs и
полнота набора не обещаются. Не обещается и совпадение engine primary между отдельными
запросами `alternates=0` и `alternates>0`; существующий режим `0` остаётся неизменным.

## 7. Метрики сравнения

Метрики считаются для каждой пары unique routes, не только относительно primary.
Дополнительно каждый alternative получает удобную сводку `vs_primary`.

### Длина и detour

Вычисленная 2D-длина polyline `L` используется как единая база сравнения. Engine summary
length остаётся рядом и должна пройти existing length audit.

- `length_delta_m = L_candidate - L_primary` — знаковая разница;
- `distance_ratio = L_candidate / L_primary`;
- `distance_ratio > detour_warning_ratio` даёт `LARGE_DETOUR`, но путь не удаляется.

Alternative может оказаться короче primary: profile preferences не равны минимизации
геометрической длины. `time_seconds` — оценка engine, не прогноз времени конкретного
бегуна. `cost` сохраняется лишь как engine diagnostic; отсутствующий cost — `null`, не
ноль и не замена длиной/временем. Нефинитные/некорректные значения не публикуются как
валидные числа. Ни cost, ни estimated time не участвуют в выборе пути для repair.

### Overlap и diversity

В 010E вводится явно названная метрика `directed_edge_weighted_v1` — сходство по
пройденной длине directed graph edges, а не геометрическое сближение линий на карте.

Для route `A` сформировать map `w_A(edge_id)` — сумма длины реально пройденных geometry
segments на этом directed edge. Длина считается по audited shape spans, а не берётся
как полная длина OSM way или без проверки копируется из `edge.length`.

Для пары `A`, `B`:

```text
S = sum(min(w_A(e), w_B(e))) по общим directed edge IDs
overlap_a = S / L_A
overlap_b = S / L_B
diversity = 1 - S / min(L_A, L_B)
```

В ответе: `shared_edge_weight_m=S`, оба направленных overlap ratios и symmetric
`diversity_ratio`. Все ratios находятся в `[0, 1]`; вычисление до presentation rounding.
Нулевая суммарная длина не подходит для такого route set: controlled audit failure,
а не деление на ноль или выдуманные ratios.

Существенные ограничения обязательны в документации и JSON metric description:

- это **edge-weight similarity**, а не точная длина пространственного пересечения;
  разные partial участки одного directed edge могут завысить `S`;
- repeated traversals сохраняют кратность в весах, но агрегированная метрика не
  различает порядок обхода; отдельно выдаётся `REPEATED_EDGE_TRAVERSAL`;
- противоположные направления одного edge не считаются общим движением;
- разные edges одного OSM way и параллельные тропы не объединяются;
- совпадение weights не делает маршруты дубликатами: нужен ordered traversal + geometry;
- эти ratios не доказывают идентичность пути, физическую plausibility или confidence.

`diversity_ratio < minimum_diversity_ratio` отмечает пару как `LOW_DIVERSITY`, но
**не скрывает** ни один unique route и не снимает ambiguity. Более точное spatial
overlap/clipped-interval comparison не входит в 010E.

## 8. Ambiguity и полнота поиска

Развести два независимых понятия:

- `AMBIGUOUS_SNAP` — не можем безопасно выбрать привязку anchor; query останавливается;
- `route_choice.status=MULTIPLE_CANDIDATES` — anchors приняты, но найдено два или больше
  unique audited routes. Query успешен, однако routing не знает, какой путь пройден.

Дополнительные значения `route_choice.status`:

- `SINGLE_CANDIDATE` — после exact deduplication остался один;
- `NOT_EVALUATED` — query не дошёл до успешного route set.

При успешном поиске всегда `search.exhaustive=false`. `SINGLE_CANDIDATE` никогда не
означает `UNIQUE_PATH`, `HIGH confidence` или разрешение auto-repair. Даже получение
всех запрошенных alternatives не доказывает полноту. Engine heuristics, profile,
границы coverage и неполнота исходных OSM data ограничивают результат.

Нужно отдельно показывать:

- сколько дополнительных вариантов запросили;
- сколько engine вернул до deduplication;
- сколько unique alternatives осталось;
- сколько exact duplicates удалено и почему;
- достигнут ли requested count после deduplication;
- причину `NO_ALTERNATIVES_RETURNED`, `FEWER_ALTERNATIVES_RETURNED` либо
  `EXACT_DUPLICATES_REMOVED`, не выдумывая топологическое объяснение.

Существенно отличающиеся и почти совпадающие пути сохраняются одинаково: сравнивать их
с activity time/distance и принимать решение о reconstruction будет Task 011.

## 9. Ответ и exit codes

Обязательные поля нового JSON:

| Поле | Содержание |
|---|---|
| `operation`, `protocol_version`, `status` | `route_alternatives`, `1`, existing domain status |
| `request` | anchors и число дополнительных маршрутов |
| `graph`, `profile`, `query_policy` | provenance и snapping/route limits 010D |
| `alternatives_policy` | version/hash, bounds, thresholds, metric/order definitions |
| `snapping` | независимые decisions обоих anchors |
| `primary_route_id` | ссылка на основной маршрут; `null` при negative outcome |
| `routes` | unique candidates: ID, role, summary, geometry, edges, audit, warnings |
| `comparisons` | все пары route IDs, metrics и advisory reasons |
| `search` | counts, duplicates, reasons, `exhaustive=false` |
| `route_choice` | status, candidate IDs; без confidence/repair eligibility |

Console показывает anchors, requested/returned counts, non-exhaustive warning и
таблицу: route ID, role, distance, delta/ratio vs primary, overlap/diversity vs primary,
audit и warnings. Для primary сравнительные значения отображаются как `—`, не как
измеренные нули. Подробности всех пар доступны в JSON.

| Outcome | Exit | Поведение |
|---|---:|---|
| `READY`, один unique route | 0 | нормальный результат; отсутствие alternatives объяснено |
| `READY`, несколько unique routes | 0 | `MULTIPLE_CANDIDATES`; routing ничего не применяет |
| `OUTSIDE_COVERAGE` / `NO_SNAP` / `AMBIGUOUS_SNAP` | 1 | `routes=[]`, `primary_route_id=null`, поиск не запускался |
| `NO_ROUTE` | 1 | snaps приняты, engine явно сообщил отсутствие пути |
| `ERROR` | 2 | invalid request/config/cache, capability, engine/parse/audit/resource failure |

Precedence snapping outcomes сохраняется из 010D. Malformed/missing `trip` без явного
engine no-path outcome — ошибка ответа, не `NO_ROUTE`. Error envelope содержит operation,
protocol version, error code и bounded details; не выдаёт частично проверенный route set.

## 10. Named policy и bounds

Предлагаемые начальные defaults; каждое значение документируется в typed config,
example TOML и provenance и получает validation/boundary tests.

| Имя | Default | Единицы | Назначение |
|---|---:|---|---|
| `maximum_requested_alternates` | 2 | count | конфигурируется в диапазоне 1..2 для текущего runtime |
| `maximum_alternatives_response_bytes` | 8388608 | bytes | bound UTF-8 route response перед JSON decode |
| `maximum_total_route_shape_points` | 48000 | count | суммарный bound всех engine candidates до deduplication |
| `maximum_total_route_edges` | 48000 | count | суммарный bound всех audits до deduplication |
| `minimum_diversity_ratio` | 0.10 | ratio | ниже — advisory `LOW_DIVERSITY`, без удаления |
| `detour_warning_ratio` | 1.50 | ratio | выше — advisory `LARGE_DETOUR`, без удаления |

`48000 = 3 × 16000` согласовано с существующими per-route bounds. `8 MiB` — начальный
лимит разбора route JSON, не лимит памяти C++ engine и не общий FIT/report limit.
`10%` и `1.5×` — прозрачные presentation thresholds, не физические константы. Границы
сравнений `<` и `>` из раздела метрик обязательны и тестируются на равенстве.

- существующие per-route distance/shape/edge limits действуют на каждый candidate;
- engine не может вернуть больше `N+1` trips: это ошибка контракта, не silent truncate;
- response byte limit применяется также отдельно к каждому `trace_attributes` response
  до JSON decode, чтобы audit не обходил bound;
- все новые числовые настройки finite; `bool`, NaN/Inf, дробные counts и неверные ranges
  отвергаются; для diversity допустимо `[0,1]`, для detour — `>=1`;
- counts запросов ограничены: 2 locate, 1 route и максимум `N+1` trace audits;
- метрики используют bounded maps: `O(K·N + K²·E)` при `K<=3`, без all-pairs scan
  координат длинных polylines и без полного graph traversal в Python;
- runtime limits engine сохраняются; их увеличение ради выполнения count запрещено;
- hard timeout in-process Actor не обещается; worker lifecycle первоначально относился
  к 010F, но [окончательное ТЗ](010f-minimal-integration-readiness.md) ограничено проверкой
  версии. Worker lifecycle отложен до unattended/server режима или обнаруженного зависания.

## 11. Тесты

### Unit и contract

- defaults, config loading/hash и каждый numeric boundary/invalid type;
- old API и CLI без flag / с `--alternates 0` не меняют single-route contract;
- primary + 0/1/2 alternatives, меньше запрошенного числа, превышение count;
- malformed primary/alternative, missing IDs, endpoint mismatch, invalid/nonfinite
  summary, shape/edge spans с дыркой или двойным учётом;
- плохой alternative не скрывается за валидным primary;
- typed result coordinates соответствуют каждому route; mutation `as_dict()` не меняет
  внутренний result;
- exact duplicate primary, duplicate alternatives, одинаковая geometry с разными edges;
- перестановка alternatives не меняет ordered semantic result/IDs/metrics;
- смена graph/profile или directed traversal меняет identity;
- метрики на синтетических весах: полное совпадение, disjoint, частичный overlap,
  asymmetric lengths, opposite directions, same way/different edges, partial edge,
  повторный проход и изменённый порядок обхода;
- отдельно фиксируется ограничение edge-weight metric для disjoint partial участков
  одного edge: такой результат не позволяет deduplication или снятие ambiguity;
- `LOW_DIVERSITY` и `LARGE_DETOUR` не удаляют candidate;
- zero-length geometry, отсутствующий cost, engine time не подменяется running pace;
- negative outcomes, error envelopes и exit codes, чистый JSON stdout;
- byte/count/aggregate resource bounds до deduplication и ограничения числа Actor calls;
- новые request policies не меняют graph identity и не требуют rebuild.

### Offline integration с реальным Valhalla

Synthetic fixtures, не пользовательские треки:

1. Один коридор: `READY`, один route, non-exhaustive/no-alternatives diagnostics.
2. Общие начало/конец и два разных обхода: минимум один реально возвращённый alternative,
   каждый с endpoint/edge audit, `MULTIPLE_CANDIDATES` и численно проверяемыми metrics.
3. Запрет foot/T4/impassable на обходе: alternatives не ослабляют profile 010C.
4. Параллельные non-equivalent snaps: прежний `AMBIGUOUS_SNAP`, route не запускается.
5. Disconnected anchors: прежний `NO_ROUTE` без придуманного пути.
6. Повторение одного запроса: стабильный semantic результат и provenance.

Не требовать от engine все теоретические пути fixture. Но хотя бы один положительный
реальный alternatives test обязателен: одних mocked responses для приёмки недостаточно.
Private probes допустимы дополнительно и остаются в ignored directories.

## 12. Acceptance criteria

- [x] Runtime probe подтвердил хотя бы один audited pedestrian alternative.
- [x] Opt-in CLI/Python API возвращают основной и до двух дополнительных маршрутов.
- [x] Старые single-route callers продолжают работать без изменения вызова/парсинга.
- [x] Каждый возвращённый candidate проверен; ни одна ошибка audit не скрыта.
- [x] Exact deduplication, stable IDs/order и pairwise metrics покрыты tests.
- [x] Limitations edge-weight overlap явно отражены и не используются как proof.
- [x] Snap ambiguity и route-choice ambiguity разделены; поиск явно non-exhaustive.
- [x] Один вариант/меньше запрошенного — нормальный audited outcome.
- [x] Counts, byte/shape/edge bounds и thresholds auditable, finite и configurable.
- [x] Current graph cache переиспользуется без rebuild; profile не ослаблен.
- [x] Routing, Core и OSM Manager tests зелёные; lint/format/type checks проходят.
- [x] README/example TOML содержат новые команды, значения полей и ограничения.
- [x] Нет изменений detection, FIT writer, reconstruction или загрузки OSM/DEM.

## 13. Файлы будущей реализации и проверки

Предполагаемая область изменений:

- `packages/osm-routing/src/warpbuster_osm_routing/models.py` — новые request/result models;
- `route_service.py` — общий audit и новый alternatives method;
- новый `alternatives.py` рядом с service — identity/deduplication/metrics/diagnostics;
- `config.py`, `cli.py`, package exports при необходимости;
- `packages/osm-routing/osm-routing.example.toml`, package `README.md`;
- routing unit/integration tests, synthetic fixtures и bounded performance regression;
- task/roadmap progress после фактической приёмки.

Команды из корня репозитория для реализации:

```bash
.venv/bin/pytest packages/osm-routing/tests
.venv/bin/pytest packages/osm-manager/tests
.venv/bin/pytest tests
.venv/bin/ruff check packages/osm-routing
.venv/bin/ruff format --check packages/osm-routing
.venv/bin/mypy --config-file packages/osm-routing/pyproject.toml packages/osm-routing/src
git diff --check
```

Полная cross-platform packaging/performance stabilization первоначально планировалась
в 010F. [Окончательное ТЗ 010F](010f-minimal-integration-readiness.md) оставляет только
проверку версии graph/runtime. Повторный packaging smoke и новый integration runner
пользователь исключил; performance hardening отложен.

## 14. Согласованные решения

1. Сохранить single-route default; alternatives включать через `--alternates 1/2`.
2. Добавить отдельный versioned result operation без breaking change старого JSON.
3. Использовать только native alternatives Valhalla, без перебора запретов/нового routing.
4. Сохранять все unique audited paths; diversity/detour — только diagnostics.
5. При ошибке audit одного engine candidate отвергать новый query целиком с объяснением.
6. Ввести честно ограниченную edge-weight similarity; точное spatial overlap отложить.
7. Не выдавать отсутствие альтернатив за доказанную уникальность или repair confidence.

## 15. Результат реализации и проверок

- Добавлены immutable `RouteAlternativesRequest`, `RouteAlternativesResult` и
  `RouteCandidate` с coordinates и defensive JSON copies; прежний API сохранён.
- Реализованы один native alternatives query, bounded per-route audit, exact
  deduplication, stable IDs/order, pairwise metrics, console table и operation v1 JSON.
- Limits/policy hash отделены от build identity; existing graph reuse проверен без rebuild.
- Native Valhalla 3.8.3 на synthetic fork вернул два пути, оба прошли `edge_walk`;
  подтверждены partial endpoint spans и distinct directed IDs при обратном проходе.
- Отдельный CLI subprocess выдаёт валидный JSON. Проверен budget: два locate,
  один route, один trace на каждый engine candidate.
- Native negative probes различают outside coverage, no snap, ambiguous snap и no route;
  bypass с `foot=no`, T4 или `impassable=yes` недоступен. Raw OSM `surface=impassable`
  не тождественен normalized engine surface: запрет последнего отдельно проверен unit
  tests. Raw-tag index/profile changes не добавлялись.
- Unit tests покрывают malformed/oversized response, повреждённый alternative при
  валидном primary, config boundaries, missing cost, duplicates/conflicting audit,
  nonfinite values, immutable results, stable ordering и ограничения weighted similarity.
- Performance regression сравнивает три synthetic polylines по 16 000 точек в bounded
  maps, без quadratic coordinate matching.
- Routing: **243 passed**; OSM Manager: **56 passed**; Core: **218 passed, 5 skipped**
  (недоступные private fixtures). Ruff check/format и mypy routing проходят.
- В routing package также нормализировано форматирование существующих файлов для
  полного `ruff format --check`; их логика вне текущей задачи не расширялась.

Acceptance criteria выполнены на локальном runtime. Поиск остаётся non-exhaustive,
метрики не доказывают маршрут спортсмена; hard timeout, multi-route exports, DEM и
применение к FIT сознательно не реализованы. Следующая итерация — Task 010F.
