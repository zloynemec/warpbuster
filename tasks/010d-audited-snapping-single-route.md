# Task 010D — Audited Snapping + Single-route API

Статус: завершена 2026-09-02.

## Простыми словами

Task 010B умеет подготовить проверенный локальный Valhalla graph, а Task 010C —
применить зафиксированный trail-running profile. Но существующий `spike` всё ещё может
молча привязать исходную координату к другой дороге или тропе далеко от неё.

Task 010D должна дать один безопасный ответ на вопрос:

> Можно ли однозначно привязать две заданные координаты к этому graph и получить между
> ними один pedestrian/trail route с проверяемой геометрией и provenance?

Если ответ неоднозначен, anchor слишком далеко от graph или пути нет, это нормальный
явный результат, а не исключение и не повод ослаблять ограничения.

## Цель

Добавить стабильный offline API и CLI для одного route между двумя WGS84 anchors:

- graph выбирается только точным `graph_id` из локального cache;
- каждый anchor проходит отдельный audited snapping до запуска route;
- удалённый или неоднозначный snap не допускается к routing;
- Valhalla route повторно проверяется относительно выбранных snaps и profile;
- результат содержит нормализованную geometry, ordered edge audit и полный provenance;
- отсутствие snap/path возвращается как versioned domain outcome;
- FIT, GPX course, Integrity Detector и reconstruction остаются вне package.

## Неизменяемые продуктовые границы

- OSM route не доказывает, что GNSS activity была повреждена.
- Routing package не читает FIT/GPX activity и не формирует `RepairPlan`.
- Task 010D возвращает ровно один route и не ищет/ранжирует alternatives.
- Timestamps, pace, recorded distance и данные конкретного спортсмена не участвуют.
- Используется только `warpbuster-trail-running-v1`; произвольных costing overrides нет.
- Network requests запрещены: graph должен быть заранее подготовлен Task 010B.

## Пользовательский контракт

```bash
warpbuster-osm-route route \
  sha256:GRAPH_DIGEST \
  --from 44.614065,33.736355 \
  --to 44.604988,33.773734

warpbuster-osm-route route \
  sha256:GRAPH_DIGEST \
  --from 44.614065,33.736355 \
  --to 44.604988,33.773734 \
  --json
```

Поддерживаются существующие `--config` и `--cache-dir`. Threshold overrides через CLI
не добавляются: они принадлежат auditable TOML/config contract.

Python boundary:

```text
RouteService.route(RouteRequest) -> RouteResult
```

`RouteRequest` содержит только `graph_id`, `start` и `end`. Объекты immutable и typed.
Публичный API не принимает raw Valhalla JSON.

## Graph capability и coverage

### Почему нужен metadata upgrade

Graph manifest v1 содержит snapshot ID и source hashes, но не содержит
`coverage.scheme/cell_ids`. По одним Valhalla tiles нельзя надёжно доказать, что anchor
лежит вне фактически загруженного OSM extract: tile крупнее Manager coverage и может
существовать для соседней области.

### Предлагаемое решение

- `Snapshot` routing package валидирует `coverage.scheme=web-mercator-v1` и непустые
  zoom-12 `cell_ids` из Manager manifest;
- новый graph manifest v2 сохраняет scheme, cell IDs, requested buffer и area в source
  provenance;
- cache key получает schema `graph-cache-key-v2`, поэтому новый capability создаёт
  новый `graph_id`, не переопределяя старый artifact;
- v1 graphs остаются доступными для `list`/`inspect` как `LEGACY_READY`;
- попытка `route` по v1 возвращает controlled error `GRAPH_CAPABILITY_MISSING` с
  указанием снова выполнить `prepare` для исходного Manager manifest;
- никакой in-place мутации или автоматического удаления v1 cache нет.

Anchor считается `OUTSIDE_COVERAGE`, только если его Web Mercator cell отсутствует в
зафиксированном множестве graph coverage. Внутри coverage отсутствие edge — `NO_SNAP`.

## Named query policy

Все значения входят в typed config, example TOML, JSON provenance и tests.

| Имя | Default | Единицы | Назначение |
|---|---:|---|---|
| `snap_search_radius_m` | 100 | m | область получения диагностических candidates |
| `maximum_snap_distance_m` | 30 | m | максимальное расстояние принятого snap |
| `equivalent_snap_separation_m` | 3 | m | близость snapped points для одной junction group |
| `snap_ambiguity_distance_delta_m` | 10 | m | насколько второй distinct candidate может быть хуже лучшего |
| `maximum_snap_candidates` | 64 | count | bound перед группировкой candidates |
| `maximum_reported_candidate_groups` | 8 | count | bound подробностей одного anchor в JSON |
| `route_endpoint_tolerance_m` | 5 | m | допустимое отличие route endpoint от audited snap |
| `maximum_route_distance_m` | 250000 | m | предел одного pedestrian route |
| `maximum_route_shape_points` | 16000 | count | предел decoded geometry и trace audit |
| `maximum_route_edges` | 16000 | count | предел ordered edge audit |
| `route_length_absolute_tolerance_m` | 10 | m | minimum tolerance geometry vs summary |
| `route_length_relative_tolerance` | 0.01 | ratio | proportional tolerance geometry vs summary |

Почему предлагается `maximum_snap_distance_m=30`: trusted outer anchor обычно должен
лежать рядом с записанной тропой; это значение оставляет запас над реальным проверенным
Orion snap около 22 m, но не разрешает переход на соседний объект в десятках метров.
Это консервативный начальный engineering threshold, а не доказанная физическая константа;
его нужно проверять на synthetic boundaries и нескольких private probes.

Hard wall-clock timeout для in-process `pyvalhalla.Actor` не заявляется в 010D:
безопасный interrupt требует отдельного worker/process lifecycle. Изначально он был
отнесён к 010F, но [окончательный объём 010F](010f-minimal-integration-readiness.md)
ограничен проверкой версии. Timeout отложен до unattended/server режима или обнаруженного зависания.
Valhalla `service_limits.pedestrian.max_distance` и собственный post-check distance
остаются обязательными bounds.

## Audited snapping

### Candidate normalization

Для каждого результата `Actor.locate` сохранить:

- input и correlated WGS84 coordinates;
- geodesic `distance_m`, независимо пересчитанный WarpBuster;
- Valhalla edge ID и OSM way ID;
- OSM node IDs, если они сохранены graph profile;
- `percent_along`, direction, Valhalla end-node ID и traversability при наличии;
- normalized `destination_only`/restriction flags, доступные в locate response;
- `use`, `surface`, `sac_scale` и pedestrian access при наличии;
- profile ID/hash и graph ID, с которыми выполнен locate.

Значение distance от Valhalla можно показывать как engine diagnostic, но решение
принимается по собственному haversine distance между input и correlated point.

### Equivalent candidates

Два directed edge не должны создавать ложную ambiguity, если это две стороны одного
физического OSM way. Candidates объединяются в одну group, когда:

1. имеют одинаковый OSM way ID и snapped points ближе
   `equivalent_snap_separation_m`; либо
2. snapped points ближе этого threshold и оба доказанно находятся у одного общего OSM
   endpoint. Это проверяется по decoded edge shape, ordered OSM node IDs и direction;
   простого пересечения списков node IDs недостаточно.

Параллельные ways без общего OSM node не объединяются, даже если визуально близки.
Алгоритм bounded: максимум 64 candidates, полный O(n²) по graph запрещён.

### Решение по anchor

Порядок проверок:

1. WGS84 validation;
2. membership в зафиксированной coverage cell;
3. наличие pedestrian candidates;
4. independently calculated distance лучшей group не больше 30 m;
5. если вторая non-equivalent group находится не дальше чем best + 10 m — anchor
   `AMBIGUOUS`;
6. иначе лучшая group становится `ACCEPTED`.

Ни `radius`, ни автоматический выбор Valhalla сами по себе не считаются enforcement:
010C уже показала, что engine способен correlated location к более далёкому component.

## Single route и post-audit

Route запускается только после двух `ACCEPTED` anchors. Request использует
`warpbuster-trail-running-v1`, ровно две break locations, `alternates=0` и отсутствие
narrative directions.

Edge audit выполняется через `trace_attributes` с `shape_match=edge_walk` по уже
полученной route polyline и тем же profile. Повторный `map_snap` запрещён: он мог бы
незаметно сопоставить готовую route geometry с другим edge и подменить provenance.

После ответа обязательно:

- decoded shape содержит минимум две конечные точки и только finite WGS84 values;
- начало/конец shape находятся не дальше `route_endpoint_tolerance_m` от audited snaps;
- geometry и summary length согласованы в пределах
  `max(route_length_absolute_tolerance_m, summary × route_length_relative_tolerance)`;
- distance, point count и edge count не превышают query limits;
- `trace_attributes` возвращает непрерывную ordered edge sequence;
- каждый normal route edge содержит Valhalla edge ID и OSM way ID;
- `travel_mode=pedestrian`, `pedestrian_type=foot`;
- `sac_scale` не превышает T3;
- `surface=impassable` отсутствует;
- `begin_shape_index/end_shape_index` валидны и монотонны;
- отсутствие обязательной provenance или расхождение endpoint —
  `ROUTE_AUDIT_FAILED`, а не частичный `READY`.

Ferry/rail-ferry не является hard profile prohibition. Если такой edge всё-таки выбран,
route остаётся `READY`, но получает structured warning `FERRY_USED`; будущий Core обязан
сам решить, допустим ли такой candidate для reconstruction.

Selected anchor с `destination_only=true` получает warning `DESTINATION_ONLY_SNAP`.
Warnings меняют `route.audit.status` с `PASS` на `WARN`, но не меняют top-level `READY`
или exit code.

Valhalla `trace_attributes` возвращает нормализованные routing attributes, а не исходные
OSM tags. Поэтому 010D не обещает различать исходные `access=private` и
`access=destination` в каждой route edge и не строит новый raw-tag index. Их engine
semantics зафиксированы profile tests 010C, но exact raw access audit остаётся явным
ограничением результата.

## Domain outcomes и exit codes

| Status | Значение | CLI exit code |
|---|---|---:|
| `READY` | оба snaps приняты, один route построен и прошёл audit | 0 |
| `OUTSIDE_COVERAGE` | хотя бы один anchor доказанно вне graph coverage | 1 |
| `NO_SNAP` | внутри coverage нет candidate не дальше 30 m | 1 |
| `AMBIGUOUS_SNAP` | есть несколько non-equivalent близких groups | 1 |
| `NO_ROUTE` | оба snaps приняты, но graph/profile не дали path | 1 |
| `ERROR` | invalid request/cache/config, incompatible graph либо audit failure | 2 |

`NO_ROUTE` означает «нет path при текущем graph и profile». Причина может быть
disconnected components или routing restriction; без отдельного connectivity API 010D
не должна угадывать более точную классификацию.

Нормальные negative outcomes возвращают полный request/graph/profile provenance и
snapping diagnostics, но `route=null`.

Оба anchors проверяются независимо, чтобы не терять диагностику второго. Если они дали
разные negative states, top-level precedence фиксирован:
`OUTSIDE_COVERAGE` → `NO_SNAP` → `AMBIGUOUS_SNAP`. Per-anchor status всегда сохраняет
точную собственную причину.

## JSON response v1

Стабильная верхняя структура:

```json
{
  "protocol_version": 1,
  "operation": "route",
  "status": "READY",
  "request": {
    "start": {"latitude": 44.614065, "longitude": 33.736355},
    "end": {"latitude": 44.604988, "longitude": 33.773734}
  },
  "graph": {
    "graph_id": "sha256:...",
    "graph_manifest_version": 2,
    "snapshot_id": "sha256:...",
    "source_sha256": ["..."],
    "materializer_schema": "...",
    "build_profile": "valhalla-pedestrian-graph-v1",
    "build_config_sha256": "...",
    "valhalla_version": "3.8.3"
  },
  "profile": {
    "profile_id": "warpbuster-trail-running-v1",
    "profile_sha256": "..."
  },
  "snapping": {
    "start": {"status": "ACCEPTED", "selected": {}, "candidate_groups": []},
    "end": {"status": "ACCEPTED", "selected": {}, "candidate_groups": []}
  },
  "route": {
    "summary": {"length_m": 4929.0, "time_seconds": 3541.3, "cost": 2865.5},
    "geometry": {
      "encoding": "polyline6",
      "encoded_polyline": "...",
      "geometry_sha256": "...",
      "point_count": 337,
      "bounds": {}
    },
    "edges": [],
    "audit": {"status": "PASS", "checks": []},
    "warnings": []
  }
}
```

`geometry` сохраняет raw Valhalla polyline6 и его SHA-256. Python model дополнительно
предоставляет decoded immutable coordinates; JSON не дублирует весь массив координат,
чтобы payload оставался bounded. Один общий `result_sha256` пока не нужен: identity
задаётся graph/profile/request, а engine geometry имеет отдельный hash.

Ordered edge содержит как минимум:

- sequence index;
- Valhalla edge ID;
- OSM way ID;
- length in metres;
- begin/end shape index;
- use, surface, sac_scale, unpaved;
- travel mode и pedestrian type.

## Ошибки инфраструктуры

Стабильные error codes с exit code 2:

- `INVALID_REQUEST`;
- `INVALID_GRAPH_ID`;
- `CACHE_NOT_FOUND` / `CACHE_CORRUPT`;
- `GRAPH_CAPABILITY_MISSING`;
- `PROFILE_ENGINE_INCOMPATIBLE`;
- `RESOURCE_LIMIT_EXCEEDED`;
- `VALHALLA_REQUEST_FAILED`;
- `ROUTE_AUDIT_FAILED`.

Valhalla exception text может находиться только в bounded diagnostic details и не
становится публичным error code.

## Ожидаемые изменения

```text
packages/osm-routing/
  osm-routing.example.toml
  README.md
  src/warpbuster_osm_routing/
    cli.py
    config.py
    graph_cache.py
    manifest.py
    models.py
    route_service.py       # новый stable boundary
    snapping.py            # bounded grouping и decisions
    valhalla_backend.py
  tests/
    test_route_service.py
    test_snapping.py
    test_route_contract.py
    test_route_behavior.py
```

Документация Task/ROADMAP обновляется. WarpBuster Core и OSM Manager не меняются.

## Test matrix

Unit tests:

- WGS84/haversine и coverage cell membership;
- candidate sorting/grouping, reverse directed edges и junction equivalence;
- parallel ways дают ambiguity;
- exact boundary 30 m и delta 10 m;
- каждый query limit и JSON serialization;
- status/exit-code mapping;
- deterministic profile/graph/request provenance.

Настоящий offline Valhalla build/Actor:

- exact path snap и один audited route;
- Orion-like 22 m snap принимается;
- candidate дальше 30 m отклоняется, даже если Valhalla готов построить route;
- запрещённый edge не превращается в удалённый fallback route;
- два disconnected components дают `NO_ROUTE`;
- T3 route проходит audit, T4 не выдаётся;
- ferry-only route возвращает `READY + FERRY_USED`;
- отсутствующий OSM way ID/сломанные shape indices моделируются как audit failure;
- повторный одинаковый request даёт тот же semantic JSON, кроме bounded engine text.

Graph compatibility:

- новый manifest v2 содержит coverage provenance;
- новый cache key не коллидирует с v1;
- v1 graph остаётся inspectable и не удаляется;
- route по v1 получает `GRAPH_CAPABILITY_MISSING`.

Private probes Orion/Andromeda допускаются только как дополнительная проверка thresholds
и не определяют тестовые ожидания.

## Acceptance criteria

- stable typed `RouteRequest`/`RouteResult` и JSON protocol v1;
- CLI `route` работает только с точным verified `graph_id`;
- coverage/outside/no-snap/ambiguity/no-route различаются без догадок;
- maximum snap distance применяется WarpBuster post-filter, а не доверяется Valhalla;
- directed variants и junctions не создают ложную ambiguity;
- parallel non-equivalent candidates не выбираются молча;
- route endpoint, geometry, limits, profile и ordered edge provenance проходят audit;
- negative domain outcomes имеют exit 1, инфраструктурные ошибки — exit 2;
- Task 010A–010C, Core и OSM Manager tests остаются зелёными;
- нет alternatives, FIT, course matching, reconstruction или network access.

## Сознательно не реализуется

- больше одного route и их overlap/diversity — Task 010E;
- heading/pace/distance evidence от activity;
- автоматическое разрешение ambiguous snap;
- raw OSM tag index для полного access audit;
- worker-process timeout и широкая performance stabilization — вне окончательного
  объёма [Task 010F](010f-minimal-integration-readiness.md), отложены;
- применение OSM geometry к ActivityData/FIT — Task 011;
- DEM и исправление высоты.

## Согласованные решения

Согласовано перед реализацией:

1. принять defaults `30 m / 10 m / 3 m` для maximum snap, ambiguity delta и equivalent
   junction separation;
2. принимать `graph_id` напрямую, без одновременной передачи Manager manifest;
3. сделать один versioned переход graph manifest/cache key к v2 ради точного coverage,
   оставив v1 inspectable legacy;
4. считать ferry route `READY` с обязательным warning, а не `NO_ROUTE`.
