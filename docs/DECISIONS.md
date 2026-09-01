# Architecture Decisions

## ADR-001 — Python

Core и CLI пишутся на Python 3.14+ из-за сильной экосистемы numerical/GIS/trajectory processing.

Статус: Accepted.

## ADR-002 — FIT-first

FIT — главный input/output формат. GPX — course/reference/export, но не промежуточный canonical representation.

Статус: Accepted.

## ADR-003 — Detector не знает course

Integrity Detector не использует GPX/OSM/course distance.

Причина: настоящий wrong turn на трейле не является GPS corruption.

Статус: Accepted.

## ADR-004 — Physical plausibility first

Основание для corruption — нарушение физической непрерывности, а не «красивость» трека.

Статус: Accepted.

## ADR-005 — Timestamps immutable during GNSS repair

Невозможная GPS-скорость не исправляется растягиванием времени.

Статус: Accepted.

## ADR-006 — Long spoofing islands are first-class

Detector должен анализировать интервалы, а не только локальные spikes.

Статус: Accepted.

## ADR-007 — Reconstruction separate from detection

Detection выдаёт corrupted interval; reconstruction отдельно выбирает способ восстановления.

Статус: Accepted.

## ADR-008 — Conservative auto-repair

LOW/MEDIUM confidence не должен автоматически менять FIT.

Статус: Accepted.

## ADR-009 — No AI in core

Алгоритм deterministic/offline.

Статус: Accepted.

## ADR-010 — OSM/routing postponed

Map-based reconstruction не входит в v0.1.

Статус: Accepted.

## ADR-011 — Private user fixtures

Реальные FIT пользователя не коммитить в public repo по умолчанию.

Статус: Accepted.

## ADR-012 — fitdecode for frame-preserving FIT decoding

FIT decoding в v0.1 выполняется через пакет `fitdecode`.

Причины:
- поддержка FIT protocol v2, developer fields и compressed timestamp headers;
- CRC validation;
- последовательный доступ к header/definition/data/CRC frames;
- сохранение offset и исходных bytes каждого frame;
- неизвестные messages/fields не требуют преобразования через GPX или другую модель.

Официальный `garmin-fit-sdk` используется в dev dependencies для генерации synthetic
fixtures, но не как основной reader: текущий Python decoder не поддерживает compressed
timestamp headers и не предоставляет столь же удобную 1:1 frame representation.

Normalized model остаётся vendor-neutral и не импортирует decoder. Reader сохраняет
исходные FIT bytes, порядок decoded messages, raw definition/data chunks и ссылку каждого
`ActivityRecord` на исходный record. Стратегия записи/patching выбирается отдельно в
Task 007; decoded objects не считаются lossless canonical representation.

Статус: Accepted.

## ADR-013 — Conservative local transition thresholds

Task 003 классифицирует только переходы между соседними records с доступной позицией.
Records без позиции учитываются отдельно и не считаются teleport; при переходе через
такой gap используется полный elapsed time исходных timestamps.
Если более сильной аномалии нет, наличие missing position или невычислимого `dt`
понижает итоговый status до `UNKNOWN`, а не создаёт ложный `CLEAN`.

Evidence разделён на два уровня:

- абсолютный предел одновременно по apparent speed и длине перехода может дать
  `IMPOSSIBLE` и HIGH confidence;
- robust-relative outlier относительно median/MAD может дать только `SUSPICIOUS` и
  LOW confidence.

Абсолютная физическая граница зависит от вида активности. Reader нормализует `sport`
и `sub_sport`, после чего detector выбирает именованный profile:

- `running`: `25 m/s` вместе с дистанцией не менее `50 m` для `IMPOSSIBLE`;
- `generic`: absolute ceiling отключён, поэтому скорость сама по себе может дать только
  `SUSPICIOUS / LOW`.

Running ceiling более чем вдвое выше средней скорости мирового рекорда 100 m
(`100 / 9.58 ≈ 10.44 m/s`) и дополнен distance floor. Источник результата:
<https://worldathletics.org/records/all-time-toplists/sprints/100-metres/all/men/senior>.

Relative floor остаётся `20 m/s` вместе с дистанцией не менее `20 m`. При наличии пяти
samples он дополнительно повышается до максимума из floor, `6 × median` и
`median + 10 × MAD`. Все значения находятся в `IntegrityConfig`, сериализуются в JSON
report и могут быть переопределены явной конфигурацией.

Course, recorded FIT speed и distance не используются как доказательство повреждения.

Статус: Accepted.

## ADR-014 — Bounded impossible-edge island search

Task 004 ищет spoofing islands только от локальных `IMPOSSIBLE` transitions. Для
каждого entry рассматривается не более 64 следующих impossible exit-кандидатов и не
более одного часа elapsed time. Поэтому detector не строит полный O(n²) reachability
graph; работа после линейного local pass ограничена числом impossible edges и фиксированным
candidate budget.

Strong interval создаётся только для структуры:

1. `A → X` локально `IMPOSSIBLE`;
2. более поздний `Y → B` локально `IMPOSSIBLE`;
3. прямой bridge `A → B` за исходный elapsed time физически правдоподобен.

Для running-profile bridge limit вычисляется из robust baseline как
`min(12 m/s, max(5 m/s, 3 × median speed))`. Поэтому длительный bridge должен быть не
только ниже абсолютного потолка, но и соотноситься с наблюдаемым темпом активности.
Affected boundaries включают все records после trusted `A` и до trusted `B`, в том числе
records без позиции. Evidence даёт HIGH confidence и причины
`impossible_transition_in`, `impossible_transition_out`, `plausible_bridge`.

Search window, candidate budget и bridge threshold находятся в `IntegrityConfig`.
Generic profile не выполняет island search, поскольку без sport-specific absolute ceiling
у него нет `IMPOSSIBLE` entry/exit evidence.

Статус: Accepted.

## ADR-015 — Safety confidence matrix and bounded diagnostics

Task 005 фиксирует матрицу итогового confidence до появления repair:

- хотя бы один локальный `IMPOSSIBLE` transition → `CORRUPTED / HIGH`;
- только baseline-relative `SUSPICIOUS` evidence → `SUSPICIOUS / LOW`;
- missing position, missing timestamp или невалидный `dt` без более сильного evidence
  → `UNKNOWN / LOW`;
- полностью нормальные данные → `CLEAN / HIGH` при достаточном baseline и
  `CLEAN / MEDIUM` при коротком baseline.

`HIGH` требует sport-specific абсолютного физического потолка. Relative outlier не
повышается до `HIGH`: смена темпа сама по себе может быть реальным движением. Corrupted
interval требует двух `IMPOSSIBLE` границ и физически правдоподобного bridge между
trusted anchors. Course отсутствует в API Integrity Detector.

Safety regression matrix включает wrong turn, out-and-back, loop, tight switchbacks,
fast downhill, stop/restart, irregular sampling, long dropout, short drift и несколько
pace regimes. Все правдоподобные траектории обязаны не быть `CORRUPTED / HIGH`, а wrong
turn обязан оставаться `CLEAN`.

Все detector thresholds и search bounds находятся в `IntegrityConfig` и описаны там с
единицами измерения. `diagnostic_max_candidate_details` (default: 100) ограничивает
хранимый sample bridge candidates; aggregate counters и количество отброшенных деталей
сохраняются. Console `-vv` дополнительно ограничен 20 строками candidate details.

Статус: Accepted.

## ADR-016 — GPX activity input is not GPX course

GPX может быть самостоятельным source activity для `inspect` и `analyze`. Он проходит
через отдельный adapter и ту же vendor-neutral `ActivityData`, что и FIT. Формат
выбирается общим dispatcher по case-insensitive suffix `.fit`/`.gpx`; неизвестный suffix
отклоняется явно.

GPX activity не преобразуется в FIT. FIT adapter, raw frames, CRC и preservation metadata
остаются отдельными и не ослабляются. GPX source хранит исходные bytes и собственные
metadata только для inspection.

`trkseg` задаёт явную границу физической непрерывности. Detector анализирует переходы
только внутри одного continuity id, поэтому пространственный разрыв между сегментами не
является teleport. GPX `<type>` маппится только для известных running/trail-running
значений; остальные значения получают generic profile. Course по-прежнему отсутствует в
API Integrity Detector.

Статус: Accepted.

## ADR-017 — Geometry gaps are advisory, not corruption evidence

Длинная последовательность почти идеально collinear observations может быть результатом
интерполяции после потери GNSS, но также может описывать настоящее движение по прямой.
Одна геометрия не доказывает происхождение точек и тем более не позволяет приписать его
Garmin, COROS, Strava или другому producer-у.

Поэтому Task 005B добавляет отдельный `possible_interpolated_gnss_gap` warning с `LOW`
confidence. Warning содержит chord/path/deviation metrics, но не участвует в вычислении
integrity status, не создаёт `CorruptedInterval` и всегда сериализуется с
`repair_eligible=false`. Файл без timestamps остаётся `UNKNOWN`, даже если warning найден.

Scan использует только activity geometry, не принимает course/OSM/DEM, не пересекает
continuity boundaries и ограничен window size, stride и retention cap из
`IntegrityConfig`. Настоящую прямую допускаем как advisory false positive; automatic
repair по этому evidence запрещён.

Статус: Accepted.

## ADR-018 — RepairPlan status describes reconstruction coverage

M5 использует GPX course только после завершённого Integrity Detection. Reader принимает
track и route geometry как отдельные continuous segments. Trusted anchors проецируются
на один segment; направление может быть forward или reverse. Несколько равноценных
course paths, unmatched anchor или неправдоподобная traversal speed не разрешаются
эвристическим выбором и остаются unresolved.

Candidate coordinates создаются только для records внутри `CorruptedInterval`.
Распределение использует первый пригодный signal из recorded distance, integrated speed,
timestamps и record order; distance/speed проходят consistency check относительно course
span и не считаются безусловной истиной.

Только plan, где все detected intervals имеют HIGH candidates, получает `READY`. Наличие
хотя бы одного unresolved или MEDIUM candidate даёт `PARTIAL`. Эти статусы описывают
полноту reconstruction evidence и не являются самостоятельной политикой writer-а.
M5 всегда dry-run: timestamps, trusted coordinates и исходный FIT не изменяются.
Reconstruction без course, OSM/DEM и FIT writer остаются вне Task 006.

Статус: Accepted.

## ADR-019 — Trusted anchors require course-independent normal context

Граница `CorruptedInterval`, найденная парой impossible transitions и plausible bridge,
ещё не доказывает, что соседний record является надёжным reconstruction anchor. Внутри
одного GNSS failure могут чередоваться jumps, короткие правдоподобные фрагменты и missing
positions; projection такого record на course способна дать убедительный, но неверный
candidate.

Поэтому до course matching каждый before/after anchor проходит directional bounded scan
по последовательным `NORMAL` transitions с внешней стороны interval. Missing position,
continuity/activity boundary и non-normal transition останавливают scan. Minimum context
и все bounds находятся в `CourseReconstructionConfig`.

При unsafe anchor близкие `IMPOSSIBLE`/`SUSPICIOUS` transitions и missing-position records
группируются в course-independent `MixedGnssRegion`. Stable outer anchors и plausible
direct bridge являются диагностическим evidence и могут дать не выше `MEDIUM`, но region
всегда остаётся `repair_eligible=false`: эти признаки не доказывают повреждение каждой
физически правдоподобной точки внутри. Course не участвует ни в stability check, ни в
построении boundaries.

Статус: Accepted.

## ADR-020 — FIT repair uses fixed-width byte patches and pre-publish diff

Полная re-encoding исходного FIT через текущий profile может потерять unknown messages,
fields, developer data или vendor-specific ordering. Поэтому M6 сохраняет original raw
container и патчит только payload существующих fixed-width scalar definitions. Размер,
definitions и порядок messages остаются неизменными; после patch пересчитывается footer
CRC.

Writer применяет каждый доступный interval candidate с confidence не ниже явно
выбранного minimum. Значения minimum — `LOW`, `MEDIUM`, `HIGH`; default — `HIGH`.
Поэтому `PARTIAL` plan может дать частичный output: выбранные intervals изменяются, а
unresolved intervals и candidates ниже threshold остаются byte-identical по coordinates.
Если не выбран ни один candidate, output не создаётся. Dry-run preview и итоговый write
report обязаны перечислять каждый detected interval с action `APPLIED`/`SKIPPED`,
confidence, наличием candidate, числом updates и стабильными reasons.

Cumulative record distance исправляется заменой increments на edges, затронутых новыми
coordinates; поддерживаемые lap/session totals и existing average speeds получают
согласованную correction. Record speed сохраняется, потому что без provenance он может
происходить от footpod/Stryd или device fusion и не является доказанно
coordinate-derived.

Для привязки summary correction writer предпочитает declared `timestamp`, когда тот
согласован с `start_time + total_elapsed_time`. При явно повреждённом end timestamp
используется derived end; timestamps в FIT остаются неизменными. Отсутствие обоих
надёжных вариантов является ошибкой записи, а не поводом угадывать границу.

Output сначала создаётся как temporary file в destination directory. До atomic publish
он повторно декодируется с CRC check, проходит normalized validation и semantic diff.
Любое изменение definitions/structure или unexpected field блокирует publish. Existing
destination не перезаписывается по умолчанию. Явный CLI-флаг `--overwrite` разрешает
atomic replacement только после тех же validation/diff checks; source FIT никогда не
может быть destination.

Статус: Accepted.

## ADR-021 — HTML reports use Leaflet and an online OSM basemap

M7 генерирует один локальный HTML-файл с embedded report data и application code, но
карта не является offline. Она загружает pinned Leaflet 1.9.4 CSS/JavaScript с
`unpkg.com` и видимые raster tiles с `tile.openstreetmap.org`. Решение принято после
практической проверки: coordinate-only Canvas без географической подложки недостаточен
для анализа GNSS ошибок.

Leaflet отвечает только за presentation. Detector и reconstruction не получают OSM
данные и сохраняют deterministic/offline-инвариант core. Browser запрашивает лишь tiles
текущего viewport без prefetch; attribution OpenStreetMap всегда видима. CSP разрешает
remote resources только от pinned Leaflet CDN и OSM tile host. Следствие для privacy:
эти сервисы получают IP пользователя и приблизительную область просмотра по tile
requests, поэтому документация больше не называет HTML report offline.

Renderer получает готовые `IntegrityReport`, `RepairPlan`, `RepairSelection` и
`FitWriteResult`. Он не повторяет detector/reconstruction decisions. Original,
candidate, actual repaired и course geometry сериализуются отдельными слоями. Missing
coordinates и `continuity_id` boundaries разрывают polyline, чтобы report не создавал
ложную solid geometry через GNSS dropout. Для читаемости полного маршрута renderer
показывает missing-position runs отдельными dashed bridges; они являются только
presentation uncertainty, отключаются как слой и никогда не пересекают continuity
boundary. Карта также показывает start/end и markers через каждый 1 km recorded
distance.

Distance/elevation audit не смешивает разные semantics: embedded FIT distance остаётся
отдельно от coordinate-derived map geometry; solid geometry отдельно исключает unknown
gap chords. FIT ascent берётся из `session.total_ascent`, а reference-course ascent явно
маркируется как unsmoothed positive GPX elevation deltas. Каждый missing run получает
отдельную строку с anchors, временем, chord и distance-stream delta.

Template и application code поставляются package resource. JSON payload экранирует HTML
control characters, а metadata выводится через DOM `textContent`. Output публикуется
атомарно и без implicit overwrite; `repair --overwrite` разрешает atomic replacement
FIT и HTML. Одинаковые inputs/config/version дают одинаковые bytes.

Статус: Accepted.

## ADR-022 — Missing-exit clusters require a complete MEDIUM proof

Один impossible jump рядом с GNSS dropout не доказывает повреждение соседнего
правдоподобного движения. One-sided detector создаёт interval только если bounded
cluster заканчивается missing-position run, внешние anchors имеют configurable NORMAL
context и plausible direct bridge, а каждый positioned-компонент внутри затронут
abnormal transition evidence. Course и reference fixed FIT запрещены при построении
границ detected core. Неполное доказательство остаётся `LOW` diagnostic; полное получает
максимум `MEDIUM`.

Course reconstruction для такого interval требует явного MEDIUM threshold. Локально
`NORMAL` detector anchors могут уже находиться внутри gradual drift, поэтому
reconstruction выполняет bounded outward scan до configurable stable course corridor.
Detected core остаётся неизменным audit evidence, а refined repair scope и новые внешние
anchors записываются отдельно. Refined anchors не изменяются: candidate использует
прямой плавный connector к course projection, course span и connector к after-anchor.
Не найденный с обеих сторон corridor даёт unresolved candidate. Distance/speed
allocation, создающая abnormal transition, заменяется timestamp allocation; remaining
impossible transition блокирует candidate.

Статус: Accepted.

## ADR-023 — Altitude anomalies are diagnostics, not coordinate proof

Running profile выполняет линейный scan соседних timestamp/altitude samples. Sustained
vertical rate и single extreme transition становятся `MEDIUM` warnings с точными
границами, delta и maximum absolute rate. Все thresholds и retention bound находятся в
`IntegrityConfig`; generic profile не угадывает универсальный вертикальный потолок.

Altitude warning не меняет integrity status и не создаёт `CorruptedInterval`:
barometric/device-fused altitude может ошибиться независимо от GNSS coordinates. Writer
не изменяет altitude на основании такого warning. Course/DEM могут позднее использовать
эту информацию только как дополнительную reconstruction validation, но не как замену
course-independent доказательству coordinate corruption.

Статус: Accepted.

## ADR-024 — Composite diagnostics and reconstruction scope are separate

Unsafe immediate anchors могут означать, что один GNSS failure состоит из нескольких
positioned и missing components. Pipeline запускает composite analysis только вокруг
уже найденного course-independent `CorruptedInterval` и расширяет bounded diagnostic
region существующими abnormal/missing evidence. Reference course не участвует ни в
границах, ни в component states.

Каждая максимальная positioned/missing component получает отдельное состояние.
Полностью покрытая detected core component является `PROVEN_CORRUPTED`; component,
затронутая abnormal transition, но не покрытая core целиком, — `TAINTED`; достаточный
чистый NORMAL context даёт `PLAUSIBLE`, иначе используется `UNKNOWN`. Stable outer
anchors и plausible bridge разрешают только попытку reconstruction и не повышают
detection confidence.

Diagnostic region, detected cores и reconstruction scope хранятся раздельно. Course
candidate может обновлять `PROVEN_CORRUPTED`, `TAINTED` и `MISSING` components;
`PLAUSIBLE/UNKNOWN` coordinates сохраняются. Поэтому scope может быть разрывным. Все
candidate-to-preserved connectors проходят physical post-check, а remaining impossible
transition блокирует candidate. Composite confidence ограничен `MEDIUM`, так что default
`HIGH` ничего не применяет.

Byte-preserving writer требует, чтобы updates точно покрывали объявленные непересекающиеся
scope ranges. Missing coordinate заполняется только при наличии position fields с FIT
invalid value в исходном message definition; добавление fields или изменение definition
запрещено lossless policy.

Статус: Accepted.
