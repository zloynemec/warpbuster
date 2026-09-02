# WarpBuster Test Strategy

## 1. Цель

Главный риск WarpBuster — не пропустить ошибку, а **испортить настоящий трек**.

Поэтому тестовая стратегия ориентирована прежде всего на false positives.

## 2. Уровни тестов

### Unit
- distance/geodesy;
- robust statistics;
- transition classification;
- bridge plausibility;
- confidence scoring;
- GPX segment matching.

### Integration
- FIT → ActivityData;
- GPX activity → ActivityData;
- analyze full activity;
- repair plan;
- FIT write/read round-trip;
- diff/validation.

### Acceptance
- private real activities;
- Garmin Connect/Strava manual compatibility вне CI.

## 3. Synthetic fixtures

Нужен генератор trajectories.

### clean_run
Последовательный бег, нормальная cadence/time.

Expected: CLEAN.

### single_spike
Одна точка улетает и возвращается.

Expected: corrupted short interval.

### spoof_island
Teleport out → 20 минут плавного ложного движения → teleport back.

Expected: единый island.

### wrong_turn
Постепенный уход на километры от hypothetical course.

Expected: CLEAN.

### loop
Настоящая петля/возврат.

Expected: CLEAN.

### trail_switchbacks
Частые резкие изменения heading.

Expected: CLEAN.

### fast_downhill
Высокая, но физически правдоподобная скорость.

Expected: CLEAN или максимум low suspicion, но не corrupted.

### gps_dropout
Position отсутствует N минут.

Expected: MISSING_GNSS interval.

### irregular_sampling
1s/5s/20s cadence mix.

Expected: detector не путает большой segment с teleport только из-за dt.

### interpolated_chord

Сотни observations лежат на длинном математически почти идеальном chord.

Expected: отдельный `LOW` geometry warning, но status не меняется и corrupted interval
не создаётся.

## 4. Private Andromeda fixture

Хранить локально, например:

`tests/private/andromeda/activity.fit`

`tests/private/andromeda/course.gpx`

Не коммитить без явного решения владельца данных.

Acceptance analyze:
- course НЕ передавать;
- основной spoof island найден;
- HIGH confidence;
- boundaries примерно совпадают с известным incident;
- tolerance сначала ±30s.

Acceptance repair:
- course передаётся только reconstruction;
- trusted records вне interval остаются неизменными;
- timestamps unchanged;
- sensor data unchanged;
- output FIT валиден;
- итоговая геометрия/дистанция разумны.

## 5. False-positive safety matrix

До появления repair synthetic regression suite обязан покрывать:

- постепенный wrong turn на километры;
- out-and-back и замкнутую петлю;
- tight switchbacks;
- быстрый непрерывный downhill;
- stop/restart и irregular sampling;
- длинный GPS dropout;
- короткий noisy drift;
- несколько правдоподобных pace regimes.

Для каждого сценария запрещён результат `CORRUPTED / HIGH` и запрещены corrupted
intervals. Wrong turn дополнительно обязан быть `CLEAN`. API detector-а не принимает
course: отклонение от GPX не является detector evidence.

## 6. Golden reports

Для synthetic fixtures можно хранить expected JSON reports.

Не golden-test-ить человекочитаемый console output посимвольно, если это не CLI contract.

## 7. Performance test

Fixture ~20k records.

Target MVP:
- `analyze` < 5 s на современном ноутбуке;
- memory bounded;
- отсутствие O(n²) full scan.

Worst-case regression с большим количеством impossible edges дополнительно проверяет,
что bridge candidate details ограничены конфигурацией, а aggregate counters не теряются.

## 8. FIT preservation regression

После repair сравнивать original vs fixed:

Expected unchanged:
- timestamp;
- HR;
- cadence;
- altitude;
- power;
- unrelated developer fields.

Expected changed:
- position в repaired interval;
- derived distance/speed fields where required;
- affected aggregates.

Любое неожиданное изменение должно появляться в diff.

## 9. GPX activity input regression

Публичные synthetic GPX fixtures проверяют:

- standard namespace и core fields;
- running/trail-running type normalization;
- несколько `trkseg` без cross-segment transition;
- clean и impossible trajectories через общий detector;
- missing timestamps как `UNKNOWN`;
- malformed XML, unsafe declarations и invalid coordinates;
- неизменность существующих FIT reader/CLI/private acceptance tests.

## 10. Geometry gap diagnostic regression

Публичные synthetic fixtures проверяют:

- warning на длинном идеальном chord с timestamps и без них;
- неизменность общего status, exit code и corrupted intervals;
- отсутствие warning на реалистично шумной прямой и плавной кривой;
- запрет объединения разных continuity segments;
- bounded candidate windows и capped retained warnings;
- сохранение target `< 5 s` на fixture из 20 000 records.

Private `Orion_Artyom.gpx` acceptance выполняется только при наличии локального файла и
не коммитит пользовательский GPX. Из-за отсутствия timestamps ожидается общий status
`UNKNOWN` и advisory warning на известном длинном chord, но не corruption.

## 11. Course reconstruction dry-run regression

Публичные synthetic fixtures проверяют:

- GPX track/route course parsing и cumulative distance;
- forward и reverse traversal;
- unique HIGH anchor match и candidate coordinates только внутри interval;
- приоритет пригодных distance/speed signals и fallback на timestamps/order;
- отказ при self-intersection ambiguity, unmatched anchors и implausible traversal;
- clean activity как `NOT_NEEDED`;
- отсутствие output FIT в любом M5 workflow;
- CLI JSON/console, exit `2` для invalid input и `3` для insufficient confidence.

Trusted-anchor safety regression дополнительно проверяет:

- isolated spike со stable NORMAL context с обеих сторон;
- блокировку anchor соседним impossible/suspicious transition;
- остановку context scan на missing position и continuity boundary;
- bounded mixed-region grouping без присоединения далёкой аномалии;
- stable outer anchors и plausible bridge как `MEDIUM`, `repair_eligible=false`;
- отсутствие GPX course в API построения safety boundary;
- console/JSON diagnostics original anchors и mixed region.

Private Andromeda acceptance проверяет основной HIGH candidate независимо от reference
fixed FIT. Reference-fixed файл используется только тестом качества: median coordinate
deviation `< 20 m`, maximum `< 40 m`. Для малого interval `8841..8854` оба исходных
anchors должны быть unsafe; диагностический mixed region `8820..9580` находит stable
outer anchors, но остаётся `MEDIUM` и не eligible. Итоговый plan — `PARTIAL`; это
запрещает автоматическое применение всего plan.

## 12. FIT writer, validation и diff regression

Публичный synthetic READY fixture проверяет:

- fixed-size byte patch без изменения размера FIT и definitions;
- новый footer CRC и успешный strict decode;
- coordinates меняются только внутри planned interval;
- cumulative distance больше не содержит teleport increments;
- lap/session total distance и existing average speed согласованы;
- невалидный summary end timestamp использует `start_time + total_elapsed_time`, не
  меняя исходные timestamps и не инвертируя correction следующих laps;
- timestamps, altitude, record speed, HR, cadence, power, temperature и developer fields
  сохраняются на 100%;
- semantic diff содержит только expected changes;
- default/explicit output, default no-overwrite, explicit atomic overwrite и
  stale-source refusal;
- `validate` exit `0/4`, `diff` unexpected changes и bounded reports;
- default `HIGH` отбрасывает MEDIUM candidate, а явные `MEDIUM`/`LOW` допускают его;
- `PARTIAL` plan применяет выбранные candidates и оставляет skipped intervals без
  изменения coordinates;
- write report перечисляет все detected intervals как applied/skipped с reasons.

Private Andromeda regression применяет основной HIGH interval из `PARTIAL` plan и
при default threshold оставляет composite region `8820..9580` неизменным. Explicit
`MEDIUM` строит один component-audited candidate, заполняет structural invalid-position
fields и не создаёт abnormal transitions. Output проходит validation/diff;
последний `record.distance`, сумма `lap.total_distance` и `session.total_distance`
согласованы; timestamps, sensors, developer и unknown fields сохраняются на 100%. Ручной
Garmin/Strava upload остаётся вне automated CI.

## 13. Interactive HTML report и packaging regression

Публичные synthetic fixtures проверяют:

- analyze HTML для FIT/GPX и repair preview/write modes;
- совместимость `--json` + `--html` без загрязнения JSON stdout;
- embedded original/candidate/repaired/course layers;
- сохранение missing coordinates как разрывов solid geometry и отдельный dashed bridge
  без пересечения continuity boundaries;
- applied/skipped decisions и FIT diff в write report;
- deterministic bytes для одинакового input;
- безопасное JSON embedding и metadata escaping;
- pinned Leaflet CDN URLs, OSM tile URL, attribution и ограничивающий CSP;
- наличие pan/zoom, scale, fit-to-track, collapsed overlay control, start/end и markers
  через каждый 1 km;
- original/course/repaired distance и ascent comparison с provenance;
- repaired FIT average pace, full/partial kilometre split pace и независимые
  ascent/descent bars, включая холм с одинаковым подъёмом и спуском внутри километра;
- missing-run audit table, chord/speed/distance delta и запрет bridge через continuity;
- default atomic no-overwrite, explicit overwrite и invalid destination errors;
- наличие HTML template внутри installed package.

Private Andromeda smoke измеряет `analyze + HTML render` для ~20 000 records с target
`< 5 s` и report size `< 5 MiB`. Fixed fixture должен перечислять 7 оставшихся
missing-position runs, включая `8893..9580`. Task 006B acceptance дополнительно требует
course-independent `MEDIUM` core `3627..3700`, proof diagnostics `2/2` tainted
components и reconstruction refinement до `3582..3741` с anchors в stable course
corridor. Candidate применяется только при explicit `MEDIUM` и не должен оставлять
abnormal transitions на этом участке; поздний composite region также применяется только
при explicit `MEDIUM`. Отдельные
vertical regressions подтверждают, что altitude anomaly видна в отчёте, но не создаёт
coordinate interval.

Task 006C synthetic regressions отдельно проверяют ordered component states,
deduplication нескольких detected cores в один planning unit, сохранение
`PLAUSIBLE/UNKNOWN` components, disjoint reconstruction scope, разделение existing и
missing coordinate updates, default skip и explicit-MEDIUM FIT write.

Task 006D synthetic regressions отдельно проверяют:

- отсутствие нового поведения без `--fill-missing-from-course`;
- независимые prefix/suffix candidates и default `HIGH` skip;
- explicit `MEDIUM` selection после однозначной observed-run alignment;
- refusal при коротком/нестабильном observed run;
- изменение только исходно invalid position fields;
- сохранение existing GPS, distance, timestamps, sensors и summaries;
- missing-completion console/JSON/HTML audit.

Private endpoint-missing regression проверяет два candidates, заполнение всех records,
valid CRC, ноль unexpected diff, 100% preservation и отсутствие abnormal transitions в
итоговом FIT. Конкретные record ranges private fixture не являются частью production
contract.

Release check отдельно строит wheel, устанавливает его с runtime dependencies в чистый
temporary Python 3.14 venv и запускает version/resource/analyze HTML smoke.
