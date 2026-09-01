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
- HTML before/after report;
- map + graphs;
- anomaly explanations;
- performance;
- CLI docs;
- packaging;
- full v0.1 Definition of Done.

Task: `tasks/008-html-report-release.md`

---

## Что делать после v0.1

Отдельные будущие epics:
- OSM reconstruction;
- DEM;
- Garmin API;
- COROS API;
- Strava proxy;
- web UI;
- automatic pipeline.

Их нельзя начинать внутри v0.1 milestones.
