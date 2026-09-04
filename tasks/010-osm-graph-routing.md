# Task 010 — Valhalla-backed Pedestrian/Trail Routing

Статус: завершена 2026-09-03; Task 010A–010F выполнены.

## Контекст

Task 009 предоставляет immutable OSM snapshots, versioned manifest и проверяемые raw
data files. Task 010A подтвердила, что Valhalla 3.8.3 может построить из них локальный
pedestrian graph, выполнить snapping/routing и вернуть OSM provenance. Поэтому M9 не
реализует собственные graph, spatial index и shortest-path algorithms.

Будущему `OSMReconstructionProvider` из M11 нужен узкий независимый контракт: получить
допустимые pedestrian/trail paths между anchors вместе с snapping diagnostics,
ambiguity и provenance. FIT и решение о применении candidate не входят в routing.

## Архитектурная граница

Routing остаётся отдельным distribution рядом с OSM Manager:

```text
OSM Manager immutable snapshot
              ↓
warpbuster-osm-routing adapter
  verified manifest → canonical PBF → cached Valhalla tiles
              ↓
audited snapping / routes / alternatives
```

Package:

- читает только публичный manifest OSM Manager и referenced blobs;
- не импортирует `warpbuster` или `warpbuster_osm_manager`;
- не выполняет network requests и не управляет source OSM cache;
- не читает FIT/GPX activity и не запускает Integrity Detector;
- не изменяет coordinates и не формирует RepairPlan;
- фиксирует snapshot, engine, profile, config и graph provenance.

Интеграция с Core остаётся в Task 012, после исправления GPX-реконструкции в Task 011.
Наличие маршрута в OSM никогда не является доказательством corruption.

## Итерации

### Task 010A — Valhalla Feasibility Spike — завершена

Проверены wheel/runtime, manifest boundary, XML/PBF materialization, tile build,
pedestrian locate/route, OSM way/node provenance, repeatability и controlled failure.

Подробности: `tasks/010a-valhalla-feasibility-spike.md`.

### Task 010B — Production Snapshot Materialization + Graph Cache

Статус: завершена 2026-09-02.

Экспериментальная materialization превращена в production boundary:

- explicit conflict detection при merge overlapping manager blobs;
- reference completeness и bounded resource limits;
- atomic derived PBF/tile publication и locking;
- cache key `(snapshot_id, Valhalla version, build/profile config hash)`;
- corruption detection, inspect и безопасный prune derived artifacts.

Routing query API в 010B не стабилизируется.

Подробное ТЗ: `tasks/010b-production-snapshot-graph-cache.md`.

### Task 010C — Versioned Pedestrian/Trail Profile

Статус: завершена 2026-09-02.

Зафиксировать и проверить Valhalla build/costing policy:

- pedestrian access и explicit prohibitions;
- trail/path/track, surface, sac_scale и incline behavior;
- barriers, ferries, steps и private/destination access;
- named request limits и table-driven synthetic regression matrix;
- profile ID/hash и объяснимые отклонения от Valhalla defaults.

Реализованы раздельные build/request profiles, immutable typed model, canonical hash,
CLI inspection и offline behavioral matrix на Valhalla 3.8.3. Выявленный риск удалённой
корреляции недоступного anchor передан в scope 010D.

Подробное ТЗ: `tasks/010c-versioned-trail-profile.md`.

### Task 010D — Audited Snapping + Single-route API

Статус: завершена 2026-09-02.

Стабилизировать offline request/response contract:

- original и snapped anchors, distances и candidate edges;
- configurable maximum snap distance;
- ambiguity diagnostics без молчаливого выбора далёкого edge;
- route geometry, length/cost и per-edge OSM provenance;
- explicit `NO_SNAP`, `NO_ROUTE`, outside-coverage и disconnected outcomes.

Подробное ТЗ: `tasks/010d-audited-snapping-single-route.md`.

### Task 010E — Alternatives + Route Diagnostics

Статус: завершена 2026-09-03.

Добавить ограниченные alternatives:

- bounded requested/returned count;
- overlap, detour и diversity metrics;
- stable ordering и explicit ambiguity;
- отсутствие альтернативы как нормальный результат;
- diagnostics, достаточные для консервативного выбора в M11.

Реализован opt-in `--alternates 1/2`, сохранение single-route contract 010D,
независимый audit каждого пути и advisory edge-weight overlap/detour/diversity без
скрытия похожих вариантов. Один найденный путь не доказывает уникальность: поиск
явно non-exhaustive, а решение об OSM reconstruction остаётся M11.

Подробное ТЗ: [Task 010E](010e-alternatives-route-diagnostics.md).

### Task 010F — Graph / Valhalla Version Guard

Статус: завершена 2026-09-03; пользователь сократил объём до проверки версии.

Единственное изменение — проверка точного совпадения Valhalla runtime с версией
сборки выбранного graph перед созданием Actor, для single-route и alternatives.
Mismatch даёт `GRAPH_ENGINE_MISMATCH` с обеими версиями и инструкцией повторить
`prepare`; graph не мутирует, `list`/`inspect` остаются доступны.

Clean-wheel smoke и новый сквозной integration runner исключены из 010F. Workers/hard
query timeout, capabilities API, platform matrix и benchmark framework также вне задачи.
Cache lifecycle, versioned API и repeatability уже реализованы и не перепроектируются.

Подробное ТЗ:
[Task 010F](010f-minimal-integration-readiness.md).

## Общие инварианты

- все thresholds и bounds именованы, документированы и покрыты tests;
- одинаковые snapshot/engine/profile/request дают stable semantic result;
- OSM object IDs и snapshot hash сохраняются в provenance;
- malformed или incomplete input не даёт частично успешный graph/path;
- отсутствие пути и неоднозначность не ослабляют access rules;
- тесты по умолчанию offline и используют synthetic fixtures;
- каждый этап сохраняет зелёными tests предыдущих этапов и OSM Manager;
- routing package не становится частью Integrity Detector.

## Полностью вне Task 010

- чтение/запись FIT;
- Integrity Detector и изменение confidence;
- выбор corrupted intervals или trusted activity anchors;
- coordinate allocation по timestamps/distance;
- OSM-only repair policy;
- DEM/elevation correction;
- map rendering, vendor APIs и web UI.

## Definition of Done M9

- Tasks 010A–010F закрыты отдельно;
- routing package устанавливается и тестируется независимо;
- snapshot безопасно превращается в cached Valhalla graph;
- versioned trail profile, snapping, route и alternatives имеют stable audit contract;
- результаты содержат provenance, достаточный для Task 012;
- Core и OSM Manager не получают routing/reconstruction imports;
- полный lint/type/test suite зелёный в проверенном окружении;
- 010F проверяет версии без повторного clean-wheel smoke; завершение M9 не обещает
  server, unattended или cross-platform production readiness. Следующий этап —
  Task 011 (GPX); локальный OSM dry-run перенесён в 012A после её acceptance.
