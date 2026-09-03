# WarpBuster Core v0.1 — Milestones

## Общий принцип

Каждый этап должен заканчиваться работающим и проверяемым состоянием проекта.

Не переходить к следующему milestone, пока:
- acceptance criteria текущего не выполнены;
- тесты зелёные;
- нет необъяснённых изменений архитектуры.

---

## M0 — Project Bootstrap

**Цель:** создать минимальный Python-проект без бизнес-логики.

Результат:
- package;
- CLI entry point;
- pytest;
- lint/type-check baseline;
- config;
- CI-ready structure.

Task: `tasks/001-project-bootstrap.md`

---

## M1 — FIT Read + Inspect

**Цель:** научиться надёжно читать FIT и видеть, что в нём находится.

Результат:
- FIT adapter;
- ActivityData;
- `warpbuster inspect`;
- synthetic/minimal FIT tests;
- private Andromeda inspect smoke test.

На этом этапе ничего не детектируем и не исправляем.

Task: `tasks/002-fit-reader-inspect.md`

---

## M2 — Local Physical Transition Detector

**Цель:** находить очевидные физически невозможные соседние переходы.

Результат:
- geodesic distance;
- dt/apparent speed;
- IntegrityConfig;
- transition classifications;
- `warpbuster analyze`;
- console + JSON;
- synthetic single-spike tests.

Не искать long islands.

Task: `tasks/003-local-transition-detector.md`

---

## M3 — Spoofing Island / Bridge Detector

**Цель:** связать невозможный вход и выход вокруг длительного плавного ложного GNSS-острова.

Результат:
- candidate island search;
- bridge plausibility;
- interval confidence/reasons;
- Andromeda analyze acceptance без GPX;
- performance guard.

Это ключевой milestone продукта.

Task: `tasks/004-spoofing-islands.md`

---

## M4 — Safety & Regression Hardening

**Цель:** доказать, что detector не ломает реальные трейловые сценарии.

Результат:
- wrong turn;
- loops;
- switchbacks;
- downhill;
- irregular cadence;
- dropout;
- confidence tuning;
- documented thresholds.

Repair всё ещё отсутствует.

Task: `tasks/005-safety-regressions.md`

---

## M4A — GPX Activity Input

**Цель:** принимать GPX как самостоятельную активность для inspection и detection.

Результат:
- отдельный GPX activity adapter;
- общий FIT/GPX input dispatcher;
- `inspect` и `analyze` для GPX;
- явные границы непрерывности `trkseg`;
- отсутствие GPX → FIT conversion.

GPX activity input не является GPX course и не добавляет course evidence в detector.

Task: `tasks/005a-gpx-activity-input.md`

---

## M4B — Geometry Gap Diagnostics

**Цель:** замечать длинные почти идеально прямые участки, похожие на синтетическую
интерполяцию между GNSS-точками, не объявляя их доказанным повреждением.

Результат:
- отдельные advisory geometry warnings;
- измеримые chord/path/deviation metrics;
- bounded scan и configurable thresholds;
- console/JSON diagnostics;
- отсутствие влияния на integrity status и repair eligibility.

Course, attribution и reconstruction отсутствуют.

Task: `tasks/005b-geometry-gap-diagnostics.md`

---

## M4C — Coordinate / Odometer Consistency Diagnostics

**Цель:** сопоставлять GNSS geometry с recorded distance stream как независимым, но не
авторитетным evidence.

Результат после получения подходящего private FIT:
- документированные semantics доступных FIT distance fields;
- bounded coordinate/odometer comparison;
- advisory warning и corroborating metrics;
- отсутствие самостоятельного влияния на corruption status;
- false-positive и performance regressions.

Milestone пока не реализуется: GPX export и скриншот недостаточны для проверки
происхождения и поведения device-recorded distance.

Task: `tasks/005c-coordinate-odometer-consistency.md`

---

## M5 — GPX Course Matching + Repair Plan (без записи FIT)

**Цель:** после HIGH-confidence corruption научиться строить безопасный план реконструкции по course.

Результат:
- отдельный GPX course reader для `trk` и `rte` geometry;
- projection trusted anchors на continuous course segments;
- forward/reverse direction и ambiguity handling;
- reconstructed coordinates candidate только внутри corrupted interval;
- `READY/PARTIAL/REFUSED/NOT_NEEDED` RepairPlan;
- console/JSON `repair --dry-run`.

FIT не изменяется. Безопасный candidate может существовать внутри `PARTIAL` plan;
политику выбора и записи определяет M6.

Task: `tasks/006-course-repair-plan.md`

---

## M5A — Trusted Anchor Safety + Mixed GNSS Regions

**Цель:** не разрешать reconstruction доверять формальным границам island detector-а,
если эти records сами находятся внутри более широкого GNSS failure.

Результат:
- directional stability scan по NORMAL transitions до и после anchors;
- отдельные причины unsafe before/after anchor;
- bounded mixed-region grouping по jumps и missing positions без course;
- diagnostic stable outer anchors и direct bridge;
- `MEDIUM/LOW`, `repair_eligible=false` для mixed regions.

Task: `tasks/006a-trusted-anchor-safety.md`

---

## M5B — One-sided GNSS Failure Clusters

**Цель:** диагностировать и консервативно реконструировать missing-exit morphology без
ослабления classic detector-а.

Результат:
- bounded course-independent one-sided proof rule;
- `MEDIUM` reconstructable interval либо `LOW` unresolved diagnostic;
- stable anchors, plausible bridge и tainted-component audit;
- explicit-MEDIUM course reconstruction с плавными anchor connectors;
- reconstruction-only expansion от detected core до stable course corridor;
- отдельные altitude-rate warnings без coordinate repair authority;
- post-check candidate transitions и timestamp fallback;
- synthetic/private Andromeda/performance regressions.

Task: `tasks/006b-one-sided-gnss-failure-clusters.md`

---

## M5C — Composite GNSS Failure Regions

**Цель:** добавить общую диагностику и консервативную reconstruction для GNSS failures,
в которых corrupted, missing, plausible и unknown components чередуются, а ближайшие
формальные anchors могут быть unsafe.

Результат:
- component-level course-independent diagnostics;
- отдельные detected evidence, mixed region и reconstruction scope;
- bounded stable outer-anchor search без доверия к anchors внутри failure;
- component-wise course candidates без изменения plausible/unknown movement;
- однозначный course match и physical post-check либо явный unresolved отказ;
- synthetic/private/performance regressions и полный console/JSON/HTML audit.

Composite candidate ограничен `MEDIUM`, а `reconstruction_scope_ranges` отделены от
полного diagnostic region. `PLAUSIBLE/UNKNOWN` positioned components не попадают в
updates; writer умеет применять разрывный scope без изменения сохранённых компонентов.

Private Andromeda `8820..9580` остаётся одним regression fixture и не определяет модель,
proof rules или thresholds.

Task: `tasks/006c-composite-gnss-failure-regions.md`

---

## M5D — Course-backed Missing-position Completion

**Цель:** явно и консервативно достраивать отсутствующую endpoint geometry по известному
course, когда длинный observed GPS run независимо физически правдоподобен и однозначно
совпадает с course.

Результат:
- отдельный opt-in provider, не создающий corrupted interval;
- prefix/suffix targets и однозначная observed-run alignment;
- recorded distance/course-span consistency и physical post-check;
- максимум `MEDIUM`, default skip и явное `--fill-missing-from-course`;
- сохранение existing GPS, timestamps, sensors и embedded FIT distance;
- объединение providers перед одним atomic writer pass;
- console/JSON/HTML audit и synthetic/private regressions.

Внутренние clean gaps, reconstruction без course и добавление новых FIT definitions не
входят в этот milestone.

Task: `tasks/006d-course-backed-missing-position-completion.md`

---

## M6 — FIT Repair Writer + Validate + Diff

**Цель:** применить RepairPlan к FIT, сохранив исходные данные.

Результат:
- output `.fixed.fit`;
- timestamps/sensors preservation;
- dependent fields/aggregates update;
- validation;
- diff;
- Andromeda fixed FIT acceptance.

Writer/validate/diff и synthetic preservation реализованы. Writer выбирает available
candidates по minimum confidence (`HIGH` default), допускает частичную запись и явно
отчитывается по каждому applied/skipped interval. Private Andromeda regression проверяет
запись основного HIGH interval без изменения unresolved region. Ручная Garmin/Strava
compatibility остаётся вне CI.

Task: `tasks/007-fit-writer-validation.md`

---

## M7 — HTML Report + v0.1 Stabilization

**Цель:** сделать удобную диагностику и подготовить ядро к реальному использованию.

Результат:
- local analyze и repair before/after HTML reports;
- Leaflet/OpenStreetMap basemap с pan/zoom/layers и локальные telemetry graphs;
- original/course/repaired metrics comparison и remaining missing-run audit table;
- corrupted/applied/skipped explanations и FIT diff;
- ~20k-record performance regression;
- CLI/docs synchronization;
- wheel resource и clean-install smoke;
- full v0.1 Definition of Done.

После завершения Task 006B HTML дополнительно показывает отдельную таблицу one-sided
clusters; residual Andromeda interval доступен как explicit-MEDIUM candidate.

Task: `tasks/008-html-report-release.md`

---

## M8 — OSM Manager

**Статус:** завершён.

**Цель:** создать отдельный локальный data-management подпроект для автоматического
получения и воспроизводимого cache сырых OSM данных, не добавляя routing в WarpBuster.

Результат:
- отдельно устанавливаемый `warpbuster-osm`;
- `ensure` по GPX, GeoJSON или bbox;
- вычисление buffered GPX corridor и bounded cache cells;
- автоматическая загрузка недостающего покрытия через configurable Overpass endpoint;
- immutable content-addressed snapshots, offline reuse, refresh и stale fallback;
- versioned JSON manifest/protocol для будущей интеграции;
- единый TOML/config contract для cache, coverage, network и resource bounds;
- `.osm`, `.osm.gz` и `.osm.pbf` import с проверкой declared coverage;
- отсутствие FIT, detection, routing и reconstruction logic.

Task: `tasks/009-osm-manager.md`

---

## M9 — Valhalla-backed Pedestrian/Trail Routing

**Статус:** завершён 2026-09-03; M9A–M9F выполнены. Следующий этап — M10 / Task 011A.

**Цель:** поверх immutable snapshots из M8 построить отдельный детерминированный adapter
к Valhalla, который соединяет заданные anchors допустимыми pedestrian/trail paths, но
не читает FIT и не принимает решений о reconstruction.

Итерации:

1. **M9A / Task 010A — Valhalla Feasibility Spike**: завершена решением `GO`; проверены
   Python 3.14 wheel, Manager snapshot materialization, offline route, provenance и
   repeatability;
2. **M9B / Task 010B — Production Snapshot Materialization + Graph Cache**: завершена;
   реализованы conflict detection, bounded merge и atomic derived-artifact cache;
   подробности: `tasks/010b-production-snapshot-graph-cache.md`;
3. **M9C / Task 010C — Versioned Pedestrian/Trail Profile**: завершена; зафиксированы
   раздельные graph/request policies, canonical profile hash и behavioral matrix;
4. **M9D / Task 010D — Audited Snapping + Single-route API**: завершена; реализованы
   stable diagnostics, thresholds, provenance и explicit failure outcomes;
5. **M9E / Task 010E — Alternatives + Route Diagnostics**: завершена; bounded
   alternatives, per-route audit, edge-weight
   overlap/diversity и non-exhaustive ambiguity diagnostics;
   подробности: [Task 010E](../tasks/010e-alternatives-route-diagnostics.md);
6. **M9F / Task 010F — Graph / Valhalla Version Guard**: завершена;
   только проверка точного совпадения версий сборки graph и текущего Valhalla перед
   routing. Packaging smoke, workers и capabilities API не входят;
   подробности: [Task 010F](../tasks/010f-minimal-integration-readiness.md).

Каждая итерация оформляется отдельным ТЗ непосредственно перед реализацией. Следующая
итерация не должна реализовываться внутри текущей. Общая декомпозиция зафиксирована в
`tasks/010-osm-graph-routing.md`; подробное первое ТЗ — в
`tasks/010a-valhalla-feasibility-spike.md`.

Результат M9:

- отдельно устанавливаемый routing package, не импортирующий WarpBuster Core;
- read-only consumption protocol v1 manifests и raw OSM blobs;
- cached Valhalla graph, versioned routing profile и audit contract;
- bounded snapping, single-route и alternative-route search;
- полный snapshot/profile/graph provenance;
- отсутствие FIT, Integrity Detector и reconstruction logic.

010F ограничен проверкой версии. После него следующий шаг — первая локальная интеграция
011A, а не unattended/server или многоплатформенная production readiness. Packaging,
широкие benchmarks и runtime workers не являются дополнительными этапами перед первым
candidate/dry-run; к hard timeout нужно вернуться до unattended batch/server режима.

Task: `tasks/010-osm-graph-routing.md`

---

## Что делать после M9

Порядок следующих epics:

1. **M10 / Task 011 — OSM Reconstruction Bridge (2D first):** сначала typed bridge и
   dry-run candidates, затем применение только однозначной 2D-геометрии без изменения
   правдоподобной FIT altitude.
2. **M11 / Task 012 — DEM-backed Elevation:** отдельные dataset/cache/provenance,
   sampling route polyline, GPX `<ele>`, elevation profile и ascent/descent policy.
3. **M12 / Task 013 — Elevation-aware OSM Reconstruction:** optional DEM evidence для
   alternatives и отдельное восстановление только missing/corrupted altitude.

Прочие будущие epics:

- Garmin API;
- COROS API;
- Strava proxy;
- web UI;
- automatic pipeline.

DEM и reconstruction нельзя начинать внутри Task 010; отсутствие DEM не должно
блокировать первоначальную 2D-интеграцию.
