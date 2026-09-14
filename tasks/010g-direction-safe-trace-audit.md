# Task 010G — Direction-safe Trace Audit for Partial Single-edge Routes

Статус: реализовано и проверено локально 2026-09-14; acceptance criteria выполнены.
Milestone: M9G — ограниченное исправление routing после локальной проверки 012B.
Предыдущие этапы: [010D](010d-audited-snapping-single-route.md),
[010E](010e-alternatives-route-diagnostics.md), [010F](010f-minimal-integration-readiness.md).
Потребитель: [012A](012a-osm-reconstruction-dry-run.md).
Выполнить до [012C — Web Hybrid Production](012c-web-hybrid-production.md).

## 1. Проблема и проверенные факты

В локальном Valhalla 3.8.3 воспроизведён сбой retracing короткого маршрута,
начало и конец которого находятся внутри одного дорожного ребра:

- `route` возвращает ненулевую polyline6: 4 точки, около 81.85 m;
- `trace_attributes(shape_match=edge_walk)` возвращает одну конечную точку,
  одно directed edge, `begin_shape_index=end_shape_index=0`, длину 0;
- `source_percent_along > target_percent_along` в этом directed edge;
- для развёрнутой исходной polyline `edge_walk` возвращает полную геометрию;
- для исходной polyline `map_snap` возвращает точную исходную геометрию и другое
  directed edge того же OSM way с возрастающим percent_along;
- `walk_or_snap` повторяет ошибочный результат и не устраняет сбой.

Это наблюдаемый контрактный дефект направления partial traversal при retracing.
Конкретная строка C++ и обобщение на все версии Valhalla пока не установлены.
Не считать проблему ошибкой FIT, недостатком OSM coverage или небольшим округлением.

Наш `_trace_edges` использует индексы trace-ответа относительно route geometry,
не запросив и не сравнив trace geometry. Проверка `geometry_weights` правильно
обнаруживает неполное покрытие и выбрасывает `ROUTE_AUDIT_FAILED`.
Single-route API также должен получить полную проверку: успешного прохождения
только `ordered_edges` недостаточно.

Приватные requests/responses и скрипты диагностического прогона находятся локально
в `tests/private/osm-dry-run-20260914/balaklava-diagnosis/`. Они не являются публичными
fixtures и не должны попадать в коммит. Публичный regression строится синтетически.

## 2. Цель и границы

Исправить получение проверяемого directed traversal для **неизменённого маршрута**
при частичном проходе по одному ребру. Не допускать принятия route/trace responses,
у которых геометрия, направление или покрытие индексами расходятся.

В scope:

1. Явный запрос и проверка trace shape.
2. Общая строгая проверка для `route()` и каждого primary/alternative в `alternatives()`.
3. Узкое распознавание воспроизведённого defective edge_walk outcome.
4. Не более одного дополнительного trace-запроса `map_snap` для такого outcome.
5. Проверка geometry identity, direction и edge coverage перед принятием fallback.
6. Версионированный audit/provenance, controlled errors и bounded resource consumption.
7. Unit, synthetic native и локальные private regression-прогоны.

Не входит: изменение Valhalla C++, обновление runtime, rebuild по новой версии,
новый pathfinding, широкая remapping-система, repair engine, confidence/selection,
FIT writer, GPX matching, DEM, acquisition, OSM Manager, web/Docker и deployment.

## 3. Неизменяемые инварианты

- Integrity Detector не получает GPX, OSM или trace diagnostics.
- Успех fallback не доказывает corruption и не подтверждает фактически пройденный путь.
- Исходные route polyline, anchors, snapping decisions, graph ID и trail profile
  остаются неизменными. Не перестраивать route ради успешного trace audit.
- Не менять timestamps, FIT coordinates, distance, altitude, sensor/developer/unknown fields.
- Сохранять graph integrity/version guard и все pedestrian/access/terrain checks.
- Непроверяемый или неоднозначный traversal → `ROUTE_AUDIT_FAILED`.
- Не удалять `complete_edge_spans`, не растягивать последний edge span до конца
  polyline и не заменять направленный ID на недоказанный «обратный» ID.
- Non-exhaustive route alternatives остаются non-exhaustive. Не добавлять selection.

## 4. Общий trace audit

`_trace_edges` должен получать shape того же trace-ответа, из которого взяты edges.
Два отдельных запроса «edges, затем shape» нельзя считать единым audit snapshot.

Для каждого успешного trace:

1. Shape существует, имеет корректный bounded polyline6 encoding и конечные WGS84 points.
2. Декодированная последовательность точек trace **точно совпадает** с исходной
   route sequence, включая порядок, число точек и обе конечные точки, на сетке polyline6.
   Допуск координат в этом task — нулевой, это identity check, не proximity heuristic.
3. Нельзя убирать duplicate points, разворачивать, упрощать, добавлять точки или
   пересчитывать indices по похожей геометрии. Даже геометрически похожая линия
   с иной дискретизацией в данном task отвергается.
4. Edge ranges в этой же sequence покрывают каждый segment ровно один раз:
   первый begin=0; begin очередного edge равен предыдущему end; последний end=N−1;
   индексы — настоящие int, не bool; значения в допустимых пределах.
5. Сохраняются положительная полная длина и существующая consistency с route summary,
   проверка graph/way IDs, ordered traversal, profile и всех запрещённых типов edges.
6. Для partial edge проверяются конечные доли и направление. Недостаток данных,
   необходимых для доказательства направления, не заменяется предположением.

Разделить получение сырого trace, нормализацию и validation так, чтобы single-route
и alternatives использовали один набор правил. Не добавлять рекурсивные вызовы
`route()`/`alternatives()` или второй запрос поиска маршрута.

## 5. Узкий fallback

Fallback разрешён только после успешных route geometry/endpoints/summary checks,
если edge_walk вернул именно воспроизведённую структурную сигнатуру:

- исходный маршрут имеет как минимум 2 точки и положительную длину;
- trace содержит ровно один edge с валидными provenance IDs;
- trace shape состоит из одной точки, равной последней точке исходного route;
- у edge begin=end=0, length=0;
- конечные finite percent_along находятся в [0, 1], source > target;
- отказ вызван этой вырожденной geometry/direction, а не неизвестным или запрещённым
  edge, profile/access violation, malformed JSON, timeout или resource limit.

В остальных случаях не пробовать map_snap: вернуть точную причину audit failure.
Сигнатура привязана к проверяемому поведению, а не безусловному разрешению по версии.

Дополнительный запрос использует ту же исходную encoded polyline, Actor/graph,
trail profile и bounded filters; меняется только `shape_match` на `map_snap`.
Не использовать `walk_or_snap` как workaround.

Fallback принимается только при одновременном выполнении:

- всех требований раздела 4;
- ровно одного корректного directed edge с ненулевым span;
- того же OSM way, что у первоначального trace; same way — необходимое,
  **но недостаточное** условие;
- доказанного возрастающего traversal по этому направленному edge:
  0 <= source < target <= 1;
- согласования направления с ориентированной геометрией этого edge и его
  topology/endpoint metadata из того же verified graph. Использовать доступные
  `locate`/edge metadata (например, `forward`, shape и endpoints), явно описав
  установленную семантику. Не выводить opposite ID арифметически;
- отсутствия иных допустимых trace traversals, конкурирующих за ту же geometry
  (включая совпадающие линии разных directed edges). Не превращать рейтинг
  `map_snap` или одно значение score в доказательство identity.

В начале реализации проверить на synthetic runtime fixture, достаточно ли доступных
metadata для directional identity и диагностики trace ambiguity. Если нет,
сохранить отказ, документировать недостающее доказательство и не объявлять task
завершённым применением одного лишь «точного shape».

Запрещены retry с увеличением radius/tolerance, fallback на другой graph/profile,
повторное snapping anchors, подмена original route trace-геометрией.

## 6. Ограничения ресурсов и конфигурация

Добавить именованный `maximum_trace_fallback_attempts_per_route`:
единица — дополнительный trace-запрос, default=1, допустимые значения только 0 или 1.
0 полностью запрещает fallback, но не отключает строгий trace audit.
Тип bool и остальные некорректные значения отвергаются; defaults/границы тестируются.

Не более одного edge_walk и одного map_snap на candidate. Если для direction audit
нужны дополнительные lookups metadata, переиспользовать результат initial snapping,
а дополнительные запросы ограничить именованным лимитом с единицами/default/test.

Ответы обеих попыток расходуют общий operation budget shape points, edges и bytes,
включая неудачный первый ответ. Ограничить размеры до декодирования/обработки.
Нельзя сбрасывать budget при fallback или оставлять дублированные большие responses
в публичном результате. Работа линейна по количеству points/edges; без O(n²) matching.
Hard timeout/native worker остаётся задачей 012C; не обещать его этим исправлением.

## 7. Provenance, ошибки и совместимость

Добавить компактный audit block с policy version, например `direction-safe-trace-v1`:

- выбранный mode и упорядоченные попытки (edge_walk, затем optional map_snap);
- trigger/rejection reason, число точек route/trace, количество edges, last end index;
- hash исходной и принятой trace geometry;
- проверки shape identity, coverage, direction и evidence metadata;
- applied fallback limit; graph/profile/runtime identities берутся из существующего envelope.

У fallback-result должна быть явная отметка `TRACE_AUDIT_FALLBACK_USED`, а не видимость
обычного edge_walk PASS. Она сама по себе не увеличивает reconstruction confidence.

Ошибки остаются `ROUTE_AUDIT_FAILED`, CLI exit=2; structured details указывают
stage/mode/check/engine_slot и ограниченные числовые diagnostics. Resource и engine
errors сохраняют собственные коды. Не заменять эти ошибки на `NO_ROUTE`/`NO_SNAP`.
Существующий operation-level abort при failed route audit сохраняется: не скрывать
плохую primary/alternative и не продолжать автоматически следующие Core gaps.

Raw coordinates, private filenames и полные responses не попадают в обычные logs;
локальные diagnostic artifacts разрешены только отдельно в ignored tests/private.

Route identity по-прежнему строится из original route geometry + **проверенного**
направленного traversal, graph/profile identity. Не использовать defective trace IDs.
Для прежнего корректного geometry/traversal route ID и ordering должны сохраниться.
Audit schema version не заменяет route identity schema; новые поля additive.
Повтор одинакового запроса даёт тот же canonical JSON, route IDs и metric values.
Обновлённый audit входит в существующий Core confirmation fingerprint; устаревшие
подтверждения не переносятся автоматически на новый audit snapshot.

## 8. Файлы реализации

Ожидаемые изменения следующего coding task:

- `packages/osm-routing/src/warpbuster_osm_routing/route_service.py`;
- `packages/osm-routing/src/warpbuster_osm_routing/alternatives.py`;
- `packages/osm-routing/src/warpbuster_osm_routing/config.py`;
- при необходимости небольшой общий `trace_audit.py` внутри того же package;
- `packages/osm-routing/tests/test_trace_audit.py` и synthetic native regression;
- существующие single-route/alternatives tests и fixtures, если новый required shape
  необходимо добавить в fake actor responses;
- `packages/osm-routing/osm-routing.example.toml`, package README;
- только при необходимости additive audit presentation — Core `report/osm.py` и tests.

Не изменять Core detector, allocation, selection, writer или web worker.
До кода уточнить конкретный список файлов и решение о directional metadata.

## 9. Тесты и acceptance criteria

1. **Synthetic native reproduction:** создать публичный небольшой OSM graph с
   curved bidirectional way. Anchors внутри одного graph edge; проверить оба
   направления. Сбойный случай воспроизводится на 3.8.3, после исправления оба
   направления дают исходную geometry, правильные directed IDs и полный coverage.
   Fixture не копирует координаты, IDs или payload приватного трека.
2. **Strict normal path:** корректный edge_walk не вызывает fallback. Single-route
   и primary/alternatives используют одинаковый полный audit.
3. **Recovery:** qualifying collapsed trace → ровно одна bounded попытка map_snap;
   идентичная geometry и доказанный direction → audited READY с fallback provenance.
4. **Refusal matrix:** сдвиг endpoint даже на одну единицу polyline6; другая point
   sequence; reversed geometry; неверный directed ID; same-way без direction proof;
   неоднозначный traversal; gaps/overlaps/index errors; missing shape; zero length;
   forbidden edges; invalid percent_along; malformed/oversized response → отказ.
   Неподходящий trigger не вызывает fallback; failure fallback не запускает новые retries.
5. **Config/resources:** default/0/1, invalid/bool, point/edge/byte budgets обеих попыток
   и число backend calls проверены; существующие global limits не обходятся.
6. **Determinism/compatibility:** repeatability, same IDs для прежних корректных paths,
   корректные overlap/diversity после recovery, graph/version/profile guards сохраняются.
   Whole-route lengths считаются по original shape с принятыми edge spans ровно один раз.
7. **Core regression:** GPX-first и candidate-only CLI работают как прежде;
   restored audit не становится разрешением reconstruction. LOW threshold не превращает
   unconfirmed OSM route в applicable candidate. Все tests 012A/012B проходят.
8. **Private acceptance, при наличии файлов:** повторить Balaklava с тем же verified
   южным graph и original FIT/GPX. Первый qualifying gap больше не даёт ошибку
   `complete_edge_spans`; последующие valid gaps доступны в общем dry-run, NO_SNAP и
   anchor refusals остаются видимыми. До/после SHA-256 восьми исходных файлов неизменны.
   Повторить также Andromeda, CHR KayaBayu и m87; сохранить JSON/HTML и сравнение
   baseline/current в ignored каталоге. Исправленные FIT не создавать.
9. **Release gate:** полный pytest Core + Manager + Routing, Ruff/mypy всех затронутых
   packages, `git diff --check` проходят. Native tests обязательны в runtime-enabled
   окружении; skipped native reproduction не считать доказательством исправления.

Отсутствующие private fixtures допускают skip с явным указанием; synthetic native
reproduction обязателен. В итоговом отчёте отделить уже выполненные проверки от skipped
и перечислить оставшиеся ограничения, включая поддержку только narrow single-edge case.

## 10. Команды проверки

Из корня репозитория, Python 3.14 с dev dependencies и совместимым Valhalla runtime:

```bash
PYTHONPATH=src:packages/osm-routing/src:packages/osm-manager/src python -m pytest -q
(cd packages/osm-routing && PYTHONPATH=src:../osm-manager/src python -m pytest -q)
(cd packages/osm-manager && PYTHONPATH=src python -m pytest -q)
python -m ruff check src tests packages/osm-routing/src packages/osm-routing/tests
PYTHONPATH=src:packages/osm-routing/src:packages/osm-manager/src python -m mypy src/warpbuster
(cd packages/osm-routing && PYTHONPATH=src:../osm-manager/src python -m mypy src)
git diff --check
```

ТЗ не разрешает deployment, изменение приватных источников или снижение safety gates.
Реализацию и результаты оформить отдельным изменением после этого specification step.


## 11. Результат реализации — 2026-09-14

Реализован общий `trace_audit.py` для single-route и alternatives: точное равенство
последовательностей polyline6 из route и trace, полное покрытие edge spans, проверка
предоставленных partial fractions и прежние проверки доступа/terrain/profile.
Нормальный `edge_walk` не вызывает дополнительные запросы. Для строго определённого
collapsed reverse partial single-edge разрешён один `map_snap` и один batched `locate`
по двум исходным концам маршрута. Направление подтверждается oriented graph shape,
двумя противоположными directed IDs, metadata topology и percent_along; конкурирующая
совместимая геометрия приводит к отказу. Route geometry и snaps не меняются.

Общий бюджет route/trace/metadata учитывает обе trace-попытки, включая отвергнутую.
Добавлены четыре именованных параметра policy, provenance и стабильные diagnostics.
Идентификаторы прежних корректных маршрутов сохраняются. Обновлены package README,
пример конфигурации и синтетические trace fixtures в существующих alternatives tests.

Добавлены 48 тестовых случаев в `packages/osm-routing/tests/test_trace_audit.py`:
synthetic native reproduction, оба направления и оба API, строгая геометрия, отказы,
неоднозначность directed traversal, ограничения ресурсов, диагностика и повторяемость.
Публичная OSM fixture синтетическая и не содержит данных приватных треков.

Проверки выполнены командами раздела 10 через локальный Python 3.14 из dev venv:

- Core: **536 passed, 17 skipped**; skips — отсутствующие private fixtures.
- Routing: **340 passed**, включая обязательный native reproduction на Valhalla 3.8.3.
- Manager: **56 passed**.
- Ruff: успешно; mypy Core (50 файлов) и Routing (20 файлов): успешно.
- `git diff --check`: успешно.

Повторены все четыре приватные пары с исходными FIT/GPX и прежними graph IDs:

| Пара | До 010G | После 010G |
| --- | --- | --- |
| Andromeda | 0 OSM-кандидатов | 0, прежние отказы snapping |
| Balaklava | ошибка trace audit | 3 OSM-кандидата, partial |
| CHR KayaBayu | 1 OSM-кандидат | 1 OSM-кандидат, partial |
| m87 | 0 OSM-кандидатов | 0, ожидаемый CLI exit 3 |

В Balaklava восстановлен audit первого gap; последующие допустимые gaps доступны,
NO_SNAP и отказы по anchors сохранены. SHA-256 восьми источников совпадают с baseline.
Coordinate dispositions, coverage, gap inventory, interval plans и selection совпадают
с baseline у всех четырёх пар. JSON внутри всех HTML валиден, `output_written=false`,
OSM `application_allowed=false`. JSON/HTML, команды, hashes и машинное сравнение
сохранены в ignored `tests/private/osm-dry-run-20260914/010g/` (`runs.json`,
`comparison.json`). Исправленные FIT не создавались.

Ограничения: fallback намеренно поддерживает только narrow single-edge signature;
при неполных metadata, изменении geometry или неоднозначном traversal остаётся отказ.
Проверен runtime 3.8.3; совместимость других версий не предполагается, version guard
сохранён. C++ Valhalla, detector, reconstruction allocation/selection, FIT writer и web
в рамках 010G не менялись. Deployment и автоматическое применение OSM не выполнялись.
