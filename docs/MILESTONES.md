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

## M5 — GPX Course Matching + Repair Plan (без записи FIT)

**Цель:** после HIGH-confidence corruption научиться строить безопасный план реконструкции по course.

Результат:
- GPX reader;
- course geometry;
- trusted anchors;
- ambiguity handling;
- reconstructed coordinates candidate;
- RepairPlan;
- `repair --dry-run`.

FIT пока не изменяется.

Task: `tasks/006-course-repair-plan.md`

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
