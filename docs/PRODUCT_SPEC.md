# WarpBuster Core — Product Specification v0.1

## 1. Проблема

Спортивные GNSS-устройства могут записывать физически невозможные координаты из-за:
- GNSS spoofing;
- jamming/reacquisition;
- GPS spikes;
- dropouts;
- иных ошибок позиционирования.

Garmin Connect и Strava могут принять такие данные как валидные и посчитать, например, километры, пройденные за секунды.

На трейлах нельзя считать любое отклонение от планового маршрута ошибкой: спортсмен действительно может потеряться и уйти на километры.

## 2. Цель

WarpBuster должен:
1. независимо от planned course определить физически недостоверные GNSS-интервалы;
2. не трогать правдоподобное движение;
3. при наличии достаточных данных предложить реконструкцию только corrupted interval;
4. сохранить максимально возможный объём исходной FIT-телеметрии.

## 3. Основные сценарии

### 3.1 Clean activity

Input: FIT без аномалий.

Expected:
- `analyze` → CLEAN;
- никаких предложений repair.

### 3.2 Single spike

Одна или несколько точек создают физически невозможный выход/возврат.

Expected:
- anomaly detected;
- небольшой corrupted interval;
- HIGH confidence при очевидной физической невозможности.

### 3.3 Long spoofing island

Структура:

A → невозможный скачок → длинный плавный ложный трек → невозможный скачок назад → B.

Внутренний ложный трек может иметь нормальную скорость и плавную геометрию.

Expected:
- detector объединяет его в один corrupted interval;
- `A → B` проверяется как plausible bridge.

### 3.4 Real wrong turn

Бегун последовательно уходит с course, бежит далеко от него и возвращается.

Expected:
- CLEAN;
- никакого snap-to-course;
- course deviation не используется как corruption evidence.

### 3.5 GPS missing

Records существуют, но position отсутствует.

Expected:
- `MISSING_GNSS`;
- не путать с spoofing;
- reconstruction — отдельная стадия.

### 3.6 GPX activity input

Input: GPX track как самостоятельная записанная активность, а не planned course.

Expected:
- `inspect` и `analyze` работают без конвертации в FIT;
- стандартные time/elevation/coordinates нормализуются в `ActivityData`;
- отдельные `trkseg` не создают ложный teleport;
- отсутствующая информация остаётся неизвестной и не угадывается.

### 3.7 Possible interpolated GNSS gap

Input: длинный плотно sampled участок почти идеально совпадает с прямой между двумя
точками; timestamps могут отсутствовать.

Expected:
- отдельный `LOW` geometry warning с измеримыми метриками;
- integrity status определяется только physical detector и не повышается;
- corrupted interval и automatic repair не создаются;
- course и предположение о vendor-е не используются.

## 4. Команды v0.1

Планируемые команды:

- `warpbuster inspect <activity.fit|activity.gpx>`
- `warpbuster analyze <activity.fit|activity.gpx>`
- `warpbuster analyze <activity.fit|activity.gpx> --json`
- `warpbuster analyze <activity.fit|activity.gpx> --html report.html`
- `warpbuster repair <fit> --course <gpx>`
- `warpbuster repair <fit> --course <gpx> --dry-run`
- `warpbuster validate <fit>`
- `warpbuster diff <original.fit> <fixed.fit>`

Команды вводятся по milestones; не все должны существовать с первого этапа.

## 5. Confidence

Каждый anomaly/interval содержит:
- level: LOW / MEDIUM / HIGH;
- список machine-readable reasons.

Примеры reasons:
- `impossible_transition_in`
- `impossible_transition_out`
- `plausible_bridge`
- `missing_position`
- `extreme_apparent_speed`
- `trajectory_discontinuity`

## 6. Reconstruction

Reconstruction запускается только после Integrity Detection.

### v0.1 provider

`CourseReconstructionProvider`

Input:
- activity;
- trusted anchor before;
- trusted anchor after;
- corrupted interval;
- GPX course.

Requirements:
- не менять trusted coordinates вне interval;
- учитывать направление/порядок course;
- не выбирать автоматически неоднозначный segment;
- LOW confidence → не auto-repair.

M5 реализует только dry-run `RepairPlan`. Course anchors проецируются на одну continuous
polyline; traversal с неправдоподобной скоростью или несколько равноценных matches
отклоняются. Candidate coordinates создаются только для records внутри уже доказанного
corrupted interval. M5 сам не пишет FIT; selection policy определяется writer-ом M6.

До projection каждый anchor обязан подтвердить устойчивость последовательными локальными
`NORMAL` transitions с внешней стороны interval. Неустойчивые anchors блокируют course
candidate. Близкие jumps/dropouts могут быть показаны как более широкий
`MixedGnssRegion`; даже при stable outer anchors и plausible bridge он не ремонтируется
автоматически, потому что внутри могут оставаться физически правдоподобные реальные точки.

Пригодная recorded distance или speed может использоваться для распределения points по
course, но только после consistency check; иначе используются timestamps или record
order. Без GPX course восстановление в M5 не выполняется.

One-sided missing-exit interval создаётся detector-ом только по course-independent proof
rule и имеет максимум `MEDIUM`. Reconstruction может использовать более широкий
MEDIUM-only anchor match, но сохраняет trusted coordinates: candidate включает плавные
anchor connectors и matched course span. Перед выдачей candidate проверяются boundary и
внутренние transitions; impossible geometry отклоняется. Применение требует явного
`--min-confidence medium`.

На стадии write доступные interval candidates выбираются по явному minimum confidence:
`LOW`, `MEDIUM` или `HIGH` (`HIGH` по умолчанию). Частичная запись разрешена: каждый
detected interval получает отдельный результат `APPLIED` или `SKIPPED`; отсутствие
candidate нельзя обойти снижением threshold.

## 7. FIT preservation

При repair необходимо по возможности сохранить:
- timestamps;
- heart rate;
- cadence;
- altitude;
- power;
- temperature;
- events;
- laps;
- sessions;
- running dynamics;
- developer fields;
- неизвестные vendor fields/messages.

После изменения position пересчитать только необходимые зависимые поля.

Writer применяет available interval candidates не ниже выбранного minimum confidence
(`HIGH` по умолчанию), включая безопасную partial application. Все applied/skipped
intervals перечисляются в report. Writer сохраняет исходные FIT frames и definitions,
патчит fixed-width coordinate/distance/поддерживаемые summary fields и публикует output
только после CRC validation и diff без unexpected changes. Existing output по умолчанию
не перезаписывается; явный `repair --overwrite` разрешает атомарную замену generated
FIT/HTML, но не исходного FIT.

Record speed по умолчанию сохраняется: без знания producer-а нельзя считать его
coordinate-derived. Average-speed summaries пересчитываются из corrected distance и
timer time только если соответствующие FIT fields уже существуют.

## 8. Не входит в v0.1

- cloud;
- Garmin/COROS API;
- Strava API;
- web/mobile UI;
- OSM reconstruction;
- DEM/terrain;
- AI;
- автоматическое изменение activities в сторонних сервисах.

## 9. Definition of Done v0.1

v0.1 готова, когда:
- FIT читается и нормализуется;
- inspect работает;
- соседние impossible transitions находятся;
- long spoofing islands находятся без course;
- synthetic wrong-turn regression остаётся clean;
- GPX course читается;
- HIGH-confidence interval может быть реконструирован по course;
- output FIT валиден;
- timestamps/sensors сохраняются;
- diff/validate и console/JSON/HTML reports работают;
- HTML открывается локально, использует Leaflet/OSM network resources для basemap,
  показывает GNSS gaps отдельными dashed bridges и не передаёт их в repair pipeline;
- HTML отдельно сравнивает embedded/geometry distance и ascent original/course/repaired,
  а также перечисляет каждый remaining missing-position run;
- wheel устанавливается в чистый Python 3.14 environment;
- private Andromeda acceptance test проходит.
