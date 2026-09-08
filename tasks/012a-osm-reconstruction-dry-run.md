# Task 012A — OSM Reconstruction Provider: Candidate Dry-run

Статус: завершена 2026-09-04.

Milestone: **M11 / Task 012 — OSM Reconstruction Bridge (2D first)**.
Предыдущие этапы: [Task 011](011-local-gpx-gap-reconstruction.md),
[011A](011a-unreachable-terminal-gnss.md),
[011B](011b-pause-aware-reconstruction.md),
[011C](011c-absent-fit-coordinate-fields.md) и [Task 010](010-osm-graph-routing.md).
Следующая итерация: **Task 012B — OSM Candidate Evidence and Selection**.

## 1. Цель

Подключить готовый Valhalla routing к provider-neutral списку `ReconstructionGap` и
получать для локально unresolved внутренних пустот один или несколько проверенных OSM
маршрутов. В 012A это только **advisory candidate discovery**:

- кандидат виден в Python API, console, JSON и HTML;
- результат содержит snapping, route audit и полный graph/OSM provenance;
- кандидат не получает reconstruction confidence и не становится `GapRepairPlan`;
- координаты маршрута не распределяются по FIT records;
- FIT writer не может применить OSM-кандидат.

Главный результат задачи — доказать корректную архитектурную связь:

```text
FIT → Integrity Detector → immutable coordinate mask / ReconstructionGap
                                      ↓
                           OSMReconstructionProvider
                                      ↓
                   typed RouteAlternativesResult from Valhalla
                                      ↓
                 advisory OSM candidates in dry-run report
```

Наличие пути в OSM не является доказательством порчи исходных координат и не доказывает,
что спортсмен двигался именно по этому пути. Integrity Detector не получает OSM,
graph ID, snapping или результаты routing.

## 2. Узкий scope 012A

Входит:

1. Typed `OSMReconstructionProvider` поверх готового gap inventory Task 011.
2. Прямой вызов публичного Python API `warpbuster-osm-routing`, без запуска его CLI и
   без промежуточного JSON-файла.
3. Запрос primary и до двух native Valhalla alternatives между двумя фактическими
   соседними preserved anchors внутренней пустоты.
4. Provider-specific immutable result, не смешанный с применимыми repair candidates.
5. Явная диагностика каждого gap, даже если запрос не запускался или путь не найден.
6. Отображение результатов в существующих console/JSON/HTML dry-run reports.
7. Opt-in CLI-интеграция только с обязательным `--dry-run`.

Не входит:

- выбор «правильного» пути, ranking по данным активности или reconstruction confidence;
- проверка соответствия route времени, pace, recorded distance или altitude;
- allocation route polyline по FIT timestamps/records и `CandidateCoordinate`;
- создание `GapRepairPlan`, selection, изменение distance stream или запись FIT;
- prefix/suffix reconstruction с одной опорой либо угадывание start/finish;
- автоматический OSM Manager `ensure`, загрузка данных, `prepare` или rebuild graph;
- GPX export маршрута, DEM и любые изменения высоты;
- новые detector heuristics, Task 005C, vendor integrations;
- hard timeout/worker process для Valhalla и server/unattended guarantees.

Эти границы нельзя обходить низким `--min-confidence`: этот параметр не применяется к
OSM-кандидатам 012A вообще.

## 3. Архитектурная граница packages

`warpbuster-osm-routing` сохраняет изоляцию и не импортирует Core. Интеграция направлена
только из Core к публичному typed routing API.

В Core определить узкий `RoutingClient` protocol, нужный provider-у, и production adapter
к `RouteService.alternatives()`. Unit tests используют fake client и не строят Valhalla
graph. Production adapter импортирует companion package лениво только при OSM opt-in:
обычные `inspect/analyze/repair` и `import warpbuster` продолжают работать без Valhalla.

Не добавлять четвёртый distribution, plugin registry, generic dependency-injection
framework или subprocess adapter. В рамках локальной интеграции companion package
устанавливается отдельно, как уже описано в README. Отсутствующий package при OSM opt-in
даёт controlled `OSM_ROUTING_UNAVAILABLE`, а не traceback. Публикация packages в PyPI и
новая packaging matrix не входят в 012A.

Provider не обращается к `GraphCache` напрямую. Production adapter загружает
`RoutingCacheConfig` тем же правилом, что CLI routing package, и передаёт точный
пользовательский `graph_id` в `RouteService`. Проверки integrity, coverage, profile и
версии Valhalla остаются ответственностью routing package.

## 4. Вход provider-а и eligibility

Provider получает один immutable snapshot:

- исходный `ActivityData`;
- уже рассчитанный `IntegrityReport` только через готовые mask/gap результаты;
- базовый `RepairPlan` Task 011 с `coordinate_mask`, `gaps`, GPX candidates и failures;
- точный `graph_id`;
- typed routing client и `OSMReconstructionConfig`.

Provider **не пересчитывает** detector, invalidation mask или gap boundaries. До и после
OSM-вызова эти значения должны быть равны. Обработка идёт по исходному порядку `plan.gaps`.

В 012A route query разрешён только если одновременно выполнено:

1. У gap нет уже построенного GPX `GapRepairPlan`. Низкий application threshold не делает
   существующий GPX candidate «отсутствующим»: OSM его не заменяет и не сравнивает.
2. Gap имеет kind `INTERNAL` и две непосредственные preserved опоры
   `anchor_before_record_index` / `anchor_after_record_index`.
3. Обе опоры по исходному mask имеют `anchor_eligible=true`, полную конечную WGS84-пару
   и один continuity segment с gap.
4. Границы не совпадают и идут в хронологическом/record порядке.
5. Не превышен общий именованный лимит OSM queries.

Изначально missing, invalidated и mixed gaps обрабатываются одинаково: origin влияет на
audit, но не доказывает выбор маршрута. Для 012A сам `--osm-graph-id` является явным
разрешением **искать и показывать** кандидаты, но не разрешением применять их.

Prefix/suffix и gap без двух trusted anchors получают `TWO_ANCHORS_REQUIRED` без запроса.
Не использовать край graph coverage, ближайшую дорогу, начало GPX или activity metadata
как выдуманную вторую опору. Поддержка явно заданных endpoints требует отдельного ТЗ.

## 5. Routing request

Для eligible gap:

- start = исходная позиция непосредственного preserved before-anchor;
- end = исходная позиция непосредственного preserved after-anchor;
- запрашивается `RouteAlternativesRequest(graph_id, start, end, N)`;
- default `N=2`: это до двух **дополнительных** путей помимо primary;
- search остаётся non-exhaustive; один candidate не означает unique route;
- результаты принимаются только из уже audited `RouteAlternativesResult`.

Не подменять anchors snapped-точками в activity и не рисовать route как продолжение
исходного трека без явных connector diagnostics. Сохранять original anchors, выбранные
snaps, расстояния привязки, route endpoints и длины обоих connectors. В 012A connectors
только отображаются и не входят в coordinate edit scope.

Typed domain outcomes `OUTSIDE_COVERAGE`, `NO_SNAP`, `AMBIGUOUS_SNAP`, `NO_ROUTE`
становятся локальным результатом одного gap; остальные gaps продолжают обрабатываться.
Нарушение graph integrity, version mismatch, invalid config или failed route audit —
ошибка всего OSM dry-run: нельзя продолжать на потенциально неисправном adapter/runtime.

## 6. Новый provider-specific контракт

Не расширять `GapRepairPlan` nullable OSM-полями. Добавить отдельные immutable модели с
канонической сериализацией, например:

- `OSMDryRunResult` — graph ID, status, ordered gap evaluations, counters и provenance;
- `OSMGapEvaluation` — исходный `gap_id`, outcome, queried flag, anchors, diagnostics,
  ordered candidates и reasons;
- `OSMRouteCandidate` — существующие `route_id`/`role`, immutable 2D polyline, route
  length, snapping/connectors, edge audit и graph/profile/snapshot provenance;
- `OSMGapOutcome` — `CANDIDATES_AVAILABLE`, `UNRESOLVED`, `NOT_QUERIED`.

Минимальные reasons:

- `GPX_CANDIDATE_ALREADY_AVAILABLE`;
- `TWO_ANCHORS_REQUIRED`, `ANCHOR_NOT_ELIGIBLE`, `CONTINUITY_MISMATCH`;
- `QUERY_LIMIT_REACHED`;
- `OUTSIDE_COVERAGE`, `NO_SNAP`, `AMBIGUOUS_SNAP`, `NO_ROUTE`;
- `RESULT_LIMIT_REACHED`;
- `OSM_ROUTING_UNAVAILABLE` для operation-level setup error.

Route IDs, candidate order и routing audit нельзя пересчитывать по упрощённой геометрии:
принимать их из routing contract 010E. Копировать/detach mutable JSON так, чтобы изменение
отчёта consumer-ом не меняло typed result. Не использовать `CandidateCoordinate`, потому
что route points ещё не сопоставлены FIT records.

`RepairPlan` остаётся базовым планом GPX/invalidation и не объявляет OSM route применимым.
Dry-run output объединяет оба результата только на уровне presentation envelope.

## 7. CLI

Расширить существующий `warpbuster repair`:

```bash
warpbuster repair activity.fit \
  --osm-graph-id sha256:GRAPH_DIGEST \
  --dry-run \
  --html activity.osm-dry-run.html
```

Дополнительные параметры:

- `--osm-graph-id GRAPH_ID` — включает OSM candidate discovery;
- `--osm-routing-config PATH` — optional явный `osm-routing.toml`;
- `--osm-cache-dir PATH` — optional override graph cache.

В 012A `--osm-graph-id` без `--dry-run` — argument error, exit 2 **до чтения/записи
output**. Routing config/cache flags без graph ID также ошибочны. `--course` может
присутствовать: сначала строится обычный локальный GPX plan, затем OSM рассматривает
только оставшиеся gaps без GPX candidate. `--fill-missing-from-course` сохраняет прежнее
значение и не включает/выключает OSM.

OSM Manager и routing graph пользователь готовит существующими командами отдельно:

```bash
warpbuster-osm ensure --gpx coverage.gpx --json > manifest.json
warpbuster-osm-route prepare manifest.json
```

Core не принимает manifest вместо graph ID и не угадывает «последний» graph.

Exit codes для OSM dry-run:

- `0` — есть хотя бы один OSM candidate set либо обычный base plan уже имеет changes;
- `3` — запросы завершены штатно, но нет OSM candidates и base plan не применим;
- `2` — arguments, dependency, config, cache, version или routing audit error.

Наличие OSM candidates не означает `repair_eligible=yes`. Console обязан отдельно
показать `OSM application: disabled in Task 012A`. FIT output не создаётся; optional
HTML остаётся единственным записываемым артефактом и соблюдает `--overwrite`.

## 8. Console, JSON и HTML

### Console

После существующего gap inventory добавить самостоятельный раздел:

```text
OSM reconstruction dry-run
Graph: sha256:...
Application: DISABLED (candidate discovery only)
G2: CANDIDATES_AVAILABLE; queried=yes; routes=2; exhaustive=no
  primary ... length=... snap=.../... connectors=.../...
  alternative_1 ... overlap=... detour=...
G3: NOT_QUERIED; reason=TWO_ANCHORS_REQUIRED
```

### JSON

Сохранить существующий repair report и добавить top-level объект
`osm_reconstruction` с собственным `protocol_version=1`, `dry_run=true`,
`application_allowed=false`, graph/snapshot/profile provenance, counters и ordered
gap evaluations. JSON stdout — один валидный документ без progress logs.

У каждого route сохранить stable ID/role, geometry, length, warnings, snapping,
connectors, edge/way provenance и pairwise diagnostics из 010E. Явно передавать
`search.exhaustive=false`; отсутствие alternatives не переименовывать в unique.

### HTML

Использовать существующий `report.html`, не создавать новый шаблон. Добавить:

- отдельные выключаемые layers OSM primary/alternatives;
- стабильную связь candidate с номером gap в таблице;
- FIT anchors, snapped endpoints и connector diagnostics разными стилями;
- route ID/role/length, snapping, warnings, overlap/detour и graph provenance;
- `candidate only / not allocated / not applied` рядом с каждой OSM geometry.

Не соединять OSM polyline с остальным track скрытой прямой. Не подменять существующий
зелёный GPX/FIT candidate layer и не показывать OSM route как repaired track.

## 9. Configuration и resource bounds

Добавить отдельный `OSMReconstructionConfig`, не расширять `IntegrityConfig` и не
дублировать routing thresholds:

- `requested_alternatives: int = 2`, допустимо `1..2`;
- `maximum_gap_queries: int = 32`, положительный bounded count;
- `maximum_total_candidate_points: int = 100_000`, положительный общий предел
  retained geometry одного dry-run.

Это operational limits, не физические thresholds и не confidence policy. Defaults
должны иметь validation/boundary tests и выводиться в JSON. При достижении query limit
оставшиеся eligible gaps сохраняются как `NOT_QUERIED/QUERY_LIMIT_REACHED`. Если audited
response превышает общий point budget, не сохранять частичную polyline: весь candidate
set этого gap становится `UNRESOLVED/RESULT_LIMIT_REACHED`.

Обход gap inventory линейный; provider не делает FIT×OSM или candidate×candidate scan.
Сложность Core-части `O(G + P)`, где `G` — bounded число gaps, `P` — суммарное число
принятых route points. Valhalla query уже ограничен Task 010, но hard wall-clock deadline
не обещается; 012A остаётся ручным локальным dry-run.

## 10. Файлы реализации

- `src/warpbuster/config.py` — `OSMReconstructionConfig`;
- `src/warpbuster/models/reconstruction.py` — provider-specific immutable results;
- `src/warpbuster/reconstruction/osm.py` — provider и narrow client protocol;
- `src/warpbuster/reconstruction/osm.py` — provider, narrow client protocol и lazy typed adapter;
- `src/warpbuster/cli.py` — opt-in arguments, dry-run gate и exit semantics;
- `src/warpbuster/report/repair.py`, `gaps.py`, `html.py` и существующий HTML asset —
  additive reporting;
- README/architecture docs;
- synthetic unit, integration, CLI/report, architecture and performance regressions.

Не менять `packages/osm-routing` без выявленного contract defect. Если defect найден,
зафиксировать его отдельно и не расширять 012A новым routing algorithm.

## 11. Обязательные тесты

### Provider unit/contract

1. Internal gap с двумя anchors создаёт ровно один alternatives request с правильным
   graph ID и исходными WGS84 endpoints.
2. Primary/alternatives сохраняют stable order, IDs, detached geometry, audit и
   provenance; один путь остаётся non-exhaustive.
3. Existing GPX candidate не вызывает OSM; application threshold этого не меняет.
4. Prefix/suffix, missing/invalid/partial/untrusted anchor, continuity mismatch и
   exceeded query limit дают явный `NOT_QUERIED` без вызова client-а.
5. Routing domain outcomes изолированы по gap; operation errors останавливают run.
6. Result point budget не допускает partial geometry.
7. Перестановка/mutation output document не меняет typed result или исходный route result.
8. Activity, IntegrityReport, mask, gaps, base candidates/failures побайтово/семантически
   неизменны; OSM ни разу не вызывается detector-ом.

### CLI/report

9. OSM flags требуют graph ID и `--dry-run`; writer mock подтверждает ноль вызовов.
10. Без OSM flags console/JSON/HTML и exit codes совпадают с текущими golden tests.
11. JSON — единый parseable document; console и HTML используют те же counters/reasons.
12. HTML различает original, GPX candidate, OSM primary/alternatives, connectors и gaps;
    нет хорды через missing records и нет repaired OSM layer.
13. Missing companion package, corrupt graph, Valhalla version mismatch и audit failure
    дают controlled exit 2 без FIT output.

### Real offline integration

14. Публичный synthetic FIT с одним internal gap + маленький synthetic OSM snapshot:
    Manager-compatible manifest → cached graph → настоящий `RouteService` → Core dry-run.
    Проверить минимум один audited candidate, graph/profile/snapshot provenance и отсутствие
    `CandidateCoordinate`/FIT output.
15. Forked synthetic OSM geometry возвращает несколько alternatives, которые все видны
    отдельно; disconnected graph даёт локальный `NO_ROUTE`.
16. Повтор одинакового run даёт одинаковый semantic JSON без зависимости от wall-clock
    timestamps; cache остаётся read-only.
17. Architecture regression подтверждает, что обычный Core code path не импортирует
    companion routing; companion загружается только при OSM opt-in.

### Регрессии и производительность

18. Полные Core, OSM Manager и routing suites остаются зелёными; private files не
    коммитятся, skips перечисляются отдельно.
19. Synthetic 20 000 records / много gaps с fake client подтверждает bounded query count,
    линейную обработку результатов и отсутствие нового detector regression.
20. Ruff, format, mypy, package import/architecture tests и `git diff --check` проходят.

## 12. Acceptance criteria

- [x] Есть отдельный typed `OSMReconstructionProvider`, не меняющий detector/mask/gaps.
- [x] Рассматриваются только unresolved internal gaps с двумя реальными trusted anchors.
- [x] Используется публичный typed alternatives API, не subprocess/CLI/JSON adapter.
- [x] Все gaps имеют явный ordered evaluation; candidates сохраняют audit/provenance.
- [x] Ни один OSM result не является `GapRepairPlan`, confidence или coordinate update.
- [x] OSM mode возможен только как explicit dry-run и не вызывает FIT writer.
- [x] Console/JSON/HTML однозначно показывают candidate-only semantics и ambiguity.
- [x] Manager acquisition и graph prepare остаются внешними явными шагами.
- [x] Default Core не требует установленного Valhalla companion package.
- [x] Все operational limits именованы, валидируются и покрыты boundary tests.
- [x] Synthetic native Valhalla end-to-end и все regression/quality suites проходят.
- [x] Не реализованы selection/allocation/application из 012B/012C и DEM из Task 013.

## 13. Предлагаемые решения для согласования

1. Первый срез поддерживает только internal gaps с двумя anchors; endpoints не угадываются.
2. OSM проверяется только после GPX planner и не конкурирует с уже найденным GPX candidate.
3. Запрашиваем alternatives сразу, но не ранжируем их в Core и не заявляем uniqueness.
4. OSM result — отдельный advisory contract; существующий `RepairPlan` не получает
   фиктивный confidence и не становится применимым.
5. Прямой typed Python adapter предпочтительнее subprocess и обмена JSON-файлами.
6. Graph acquisition/build остаётся отдельным пользовательским действием.
7. Task 012A заканчивается на отчёте. Evidence/selection — 012B, allocation/write — 012C.
