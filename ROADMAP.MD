# WarpBuster Core v0.1 — Roadmap

Roadmap следует порядку milestone из [`docs/MILESTONES.md`](docs/MILESTONES.md).
Новый этап начинается только после выполнения acceptance criteria текущего и
успешного прохождения всех тестов.

## Прогресс

- [x] **M0 / Task 001 — Project Bootstrap**
  ([task](tasks/001-project-bootstrap.md))
  - Python 3.14+ package с `src/` layout;
  - CLI entry point, тесты, lint, format и type-check;
  - базовая структура проекта и правила для приватных FIT-файлов.

- [x] **M1 / Task 002 — FIT Reader + Inspect**
  ([task](tasks/002-fit-reader-inspect.md))
  - vendor-neutral `ActivityData` и FIT reader adapter;
  - `warpbuster inspect` с console и JSON output;
  - сохранение metadata и исходных FIT frames для будущего lossless patching;
  - synthetic tests и private smoke tests для Andromeda FIT.

- [x] **M2 / Task 003 — Local Physical Transition Detector**
  ([task](tasks/003-local-transition-detector.md))
  - консервативная классификация соседних переходов;
  - geodesic distance, apparent speed и централизованный `IntegrityConfig`;
  - `warpbuster analyze` без использования GPX course.

- [x] **M3 / Task 004 — Long Spoofing Islands + Bridge Plausibility**
  ([task](tasks/004-spoofing-islands.md))
  - поиск длительных ложных GNSS-островов;
  - bridge plausibility, corrupted intervals и confidence/reasons;
  - ограниченный по сложности поиск и проверка производительности.

- [x] **M4 / Task 005 — Safety / False Positive Hardening**
  ([task](tasks/005-safety-regressions.md))
  - regression-набор для правдоподобных сложных траекторий;
  - настройка confidence и документирование thresholds;
  - bounded `-vv` diagnostics и performance regression;
  - защита от false positive до появления repair.

- [ ] **M5 / Task 006 — GPX Course Matching + Dry Repair Plan**
  ([task](tasks/006-course-repair-plan.md))
  - GPX reader, trusted anchors и ambiguity handling;
  - reconstruction candidates и `RepairPlan`;
  - `repair --dry-run` без изменения исходного FIT.

- [ ] **M6 / Task 007 — FIT Writer, Preservation, Validate, Diff**
  ([task](tasks/007-fit-writer-validation.md))
  - безопасное применение `RepairPlan`;
  - сохранение timestamps, sensors и неизвестных данных;
  - повторная валидация FIT и подробный diff изменений.

- [ ] **M7 / Task 008 — HTML Report + v0.1 Stabilization**
  ([task](tasks/008-html-report-release.md))
  - локальный HTML-отчёт с картой, графиками и объяснением аномалий;
  - CLI/docs/packaging cleanup и полный regression/performance check;
  - выпуск WarpBuster Core v0.1.

## После v0.1

OSM/DEM reconstruction, Garmin/COROS/Strava integrations, web UI и automatic
pipeline остаются отдельными будущими направлениями и не входят в задачи выше.
