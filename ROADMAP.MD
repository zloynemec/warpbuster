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

- [x] **M4A / Task 005A — GPX Activity Input**
  ([task](tasks/005a-gpx-activity-input.md))
  - отдельный GPX adapter и общий FIT/GPX activity dispatcher;
  - `inspect` и `analyze` для GPX без конвертации в FIT;
  - безопасные границы непрерывности между `trkseg`;
  - FIT regression и GPX parser/CLI tests.

- [x] **M4B / Task 005B — Geometry Gap Diagnostics**
  ([task](tasks/005b-geometry-gap-diagnostics.md))
  - advisory detection длинных почти идеальных GNSS chords;
  - отдельные `LOW` geometry warnings без изменения integrity status;
  - bounded scan, configurable thresholds и false-positive regressions;
  - private acceptance для Strava GPX без timestamps.

- [ ] **M4C / Task 005C — Coordinate / Odometer Consistency Diagnostics**
  ([task](tasks/005c-coordinate-odometer-consistency.md)) — ожидает приватный FIT fixture
  - сравнение GNSS geometry с независимо записанной distance stream;
  - advisory/corroborating evidence без самостоятельного повышения status;
  - неизвестный источник distance не приписывается Garmin, Stryd или другому vendor-у;
  - реализация отложена до исследования реального FIT.

- [x] **M5 / Task 006 — GPX Course Matching + Dry Repair Plan**
  ([task](tasks/006-course-repair-plan.md))
  - GPX course reader, trusted anchors и ambiguity handling;
  - reconstruction candidates и `RepairPlan`;
  - `repair --dry-run` без изменения исходного FIT;
  - writer и политика применения оставлены следующему milestone M6.

- [x] **M5A / Task 006A — Trusted Anchor Safety + Mixed GNSS Regions**
  ([task](tasks/006a-trusted-anchor-safety.md))
  - directional NORMAL-context validation до обращения к course;
  - bounded grouping соседних jumps и missing-position gaps;
  - внешние stable anchors и plausible bridge только как диагностика;
  - mixed region остаётся `MEDIUM/LOW`, `repair_eligible=false`.

- [x] **M5B / Task 006B — One-sided GNSS Failure Clusters**
  ([task](tasks/006b-one-sided-gnss-failure-clusters.md))
  - residual Andromeda cluster `3626..3700`, видимый примерно на `6.6..7.0 км`;
  - bounded detection для impossible/suspicious evidence рядом с GNSS dropouts без
    классической пары entry/exit transitions;
  - course-independent proof rule и явные reconstructable/unresolved diagnostics;
  - доказанный interval имеет максимум `MEDIUM` и применяется только при явном
    `--min-confidence medium`;
  - плавные anchor connectors не изменяют trusted records, а post-check блокирует
    candidate с физически невозможными переходами.
  - reconstruction-only corridor refinement расширяет detected core `3627..3700` до
    полного drift scope `3582..3741`; исходные и refined boundaries аудируются отдельно;
  - altitude-rate warnings показываются отдельно и сами по себе не доказывают порчу
    координат.

- [x] **M5C / Task 006C — Composite GNSS Failure Regions**
  ([task](tasks/006c-composite-gnss-failure-regions.md))
  - общая component-level модель для чередующихся corrupted, missing, plausible и
    unknown участков внутри одного GNSS failure;
  - course-independent detected evidence отдельно от diagnostic region и
    reconstruction scope;
  - bounded stable-anchor search и безопасная component-wise reconstruction;
  - private Andromeda `8820..9580` используется только как regression fixture;
  - disjoint reconstruction scope сохраняет `PLAUSIBLE/UNKNOWN` positioned components;
  - missing coordinates и replacements существующих координат аудируются отдельно;
  - default `HIGH` не применяет composite candidate, явный `MEDIUM` включает его.

- [x] **M5D / Task 006D — Course-backed Missing-position Completion**
  ([task](tasks/006d-course-backed-missing-position-completion.md))
  - отдельный opt-in planner для отсутствующих координат в начале/конце активности;
  - однозначное сопоставление устойчивого observed GPS run с reference course;
  - независимое объединение с corruption repair перед одной атомарной записью;
  - максимум `MEDIUM`, existing GPS/timestamps/sensors и FIT distance сохраняются;
  - console/JSON/HTML audit и synthetic/private regressions.

- [x] **M6 / Task 007 — FIT Writer, Preservation, Validate, Diff**
  ([task](tasks/007-fit-writer-validation.md))
  - byte-preserving writer, CRC, atomic output, default no-overwrite и явный
    `--overwrite` для generated FIT/HTML реализованы;
  - timestamps, sensors, developer/unknown data проверяются semantic diff;
  - `validate` и `diff` реализованы, synthetic preservation regression зелёный;
  - partial repair выбирает candidates по `--min-confidence` (`HIGH` по умолчанию), а
    отчёт перечисляет applied/skipped intervals;
  - private Andromeda regression записывает основной HIGH interval и сохраняет
    unresolved region; ручная Garmin/Strava compatibility остаётся внешней проверкой.

- [x] **M7 / Task 008 — HTML Report + v0.1 Stabilization**
  ([task](tasks/008-html-report-release.md))
  - локальные analyze/repair HTML reports с картой, графиками и anomaly explanations;
  - Leaflet/OSM basemap с zoom, safe gap rendering и default no-overwrite;
  - distance/elevation comparison и audit table оставшихся missing-position runs;
  - CLI/docs/packaging cleanup, clean wheel install и regression/performance checks;
  - WarpBuster Core v0.1 включает завершённую Task 006B и отдельную таблицу one-sided
    GNSS diagnostics.

## После v0.1

- [x] **M8 / Task 009 — OSM Manager**
  ([task](tasks/009-osm-manager.md))
  - отдельный устанавливаемый подпроект и CLI `warpbuster-osm`;
  - `ensure --gpx/--geojson/--bbox` с автоматической bounded-загрузкой через Overpass;
  - immutable content-addressed snapshots, coverage cache и offline reuse;
  - versioned JSON protocol для будущего вызова из WarpBuster;
  - `.osm`/`.osm.gz`/`.osm.pbf` import, cache maintenance и diagnostics;
  - все operational limits вынесены в отдельный TOML/config contract;
  - без routing, reconstruction и изменений WarpBuster Core.

- [x] **M9 / Task 010 — Valhalla-backed Pedestrian/Trail Routing**
  ([task](tasks/010-osm-graph-routing.md)) — итерации 010A–010F завершены 2026-09-03
  - [x] **M9A / Task 010A — Valhalla Feasibility Spike**
    ([task](tasks/010a-valhalla-feasibility-spike.md)) — `GO`, завершена 2026-09-02;
  - [x] **M9B / Task 010B — Production Snapshot Materialization + Graph Cache**
    ([task](tasks/010b-production-snapshot-graph-cache.md)) — завершена 2026-09-02;
  - [x] **M9C / Task 010C — Versioned Pedestrian/Trail Profile**
    ([task](tasks/010c-versioned-trail-profile.md)) — завершена 2026-09-02;
  - [x] **M9D / Task 010D — Audited Snapping + Single-route API**
    ([task](tasks/010d-audited-snapping-single-route.md)) — завершена 2026-09-02;
  - [x] **M9E / Task 010E — Alternatives + Route Diagnostics**
    ([task](tasks/010e-alternatives-route-diagnostics.md)) — завершена 2026-09-03;
    opt-in до двух alternatives, per-route audit, edge-weight
    overlap/diversity, stable IDs и явная неполнота поиска;
  - [x] **M9F / Task 010F — Graph / Valhalla Version Guard**
    ([task](tasks/010f-minimal-integration-readiness.md)) — завершена 2026-09-03;
    только проверка точного совпадения версий graph/runtime перед routing;
    без packaging smoke, runtime workers и capabilities API;
  - Valhalla заменяет собственные graph/index/pathfinding; OSM Manager остаётся слоем
    acquisition/cache/snapshot provenance;
  - OSM не используется в Integrity Detector.

- [ ] **M10 / Task 011 — OSM Reconstruction Bridge (2D first)** — предварительный план,
  ТЗ ещё не оформлено
  - **011A:** отдельный `OSMReconstructionProvider`, принимающий только результаты
    Integrity Detector и typed `RouteResult`, сначала в candidate/dry-run режиме;
  - **011B:** сопоставление trusted anchors, timestamps и recorded distance с одним или
    несколькими OSM route candidates без изменения FIT;
  - **011C:** консервативное применение только однозначных 2D-маршрутов с высокой
    уверенностью; меняются координаты и необходимые агрегаты, но не правдоподобная
    исходная altitude;
  - GPX-course и OSM-only режимы остаются разными reconstruction strategies;
  - ambiguous/low-confidence варианты остаются unresolved;
  - полный audit candidate geometry, confidence, applied/skipped участков и OSM
    provenance в console/JSON/HTML reports.

- [ ] **M11 / Task 012 — DEM-backed Elevation** — отдельная optional подсистема
  - **012A:** выбрать DEM dataset, лицензию, resolution и зафиксировать vertical datum;
  - **012B:** immutable tile cache с coverage, hashes, offline reuse и bounded download;
  - **012C:** детерминированный sampler для route polyline, bounded interpolation и
    явные missing-coverage diagnostics;
  - **012D:** GPX route export с `<ele>`, raw/smoothed elevation profiles и расчётом
    ascent/descent по именованной filtering policy;
  - сравнивать DEM-профиль с сохранённой FIT/course altitude как независимое evidence;
  - DEM не используется Integrity Detector и не заменяет правдоподобные FIT-высоты.

- [ ] **M12 / Task 013 — Elevation-aware OSM Reconstruction** — после M10 и M11
  - использовать DEM-профиль как optional признак для ранжирования alternatives;
  - объединять geometry, time, recorded distance/pace и elevation evidence в
    консервативный confidence без привязки к конкретной тренировке;
  - разрешить заполнение altitude только для отдельно доказанных missing/corrupted
    значений; coordinate repair и altitude repair остаются разными решениями;
  - отсутствие DEM или несовпадение vertical datum не блокирует однозначный 2D repair;
  - неоднозначные кандидаты не применяются автоматически.

Таким образом, routing завершён, а ближайший путь остаётся двухмерным: подключить его к
repair в dry-run и проверить однозначные восстановления. DEM добавляется после этого
как независимый enrichment/evidence layer до расширенного автоматического выбора.
Эксплуатационное усиление routing (hard query timeout/workers, широкие benchmarks,
другие платформы и capabilities negotiation) вынесено за объём 010F:
возвращаться по измеренной потребности, а к timeout — до unattended batch/server режима.
Это не дополнительные обязательные этапы перед первым локальным 011A dry-run.
Garmin/COROS/Strava integrations, web UI и automatic pipeline остаются отдельными
будущими направлениями.
