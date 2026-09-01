# Task 006C — Composite GNSS Failure Regions

Статус: выполнена.

## Проблема

Реальный GNSS failure не всегда образует один непрерывный spoofing island или один
one-sided cluster. Внутри одного ограниченного периода могут чередоваться:

- физически невозможные координаты;
- подозрительные positioned records;
- локально правдоподобные positioned records;
- короткие и длинные missing-position runs;
- кратковременные возвраты GNSS;
- точки с неизвестным качеством, для которых недостаточно evidence.

Ближайшие records вокруг уже доказанного corrupted interval в таком случае могут сами
принадлежать более широкому failure. Они небезопасны как reconstruction anchors, даже
если формально ограничивают classic island.

Существующие classic и one-sided detectors покрывают отдельные morphology, но не дают
унифицированного component-level представления составного failure region. В результате
pipeline может доказать corruption малого core, найти устойчивые точки дальше снаружи,
но не иметь безопасного способа описать или реконструировать всё, что находится между
ними.

## Цель

Добавить vendor-neutral модель, диагностику и reconstruction planning для составных
GNSS failure regions без ослабления главного правила:

> Never modify plausible movement. Repair only demonstrably impossible GNSS data.

Task должна:

1. независимо от reference course разбивать bounded failure region на компоненты;
2. отдельно оценивать evidence каждой positioned-компоненты;
3. отличать доказанную corruption от missing, plausible и unknown data;
4. находить устойчивые внешние anchors без объявления всего промежутка corrupted;
5. на отдельной reconstruction stage проверять, можно ли безопасно построить candidate
   по reference course;
6. изменять только records, для которых разрешение на update объяснимо и аудируемо;
7. явно отказываться от reconstruction, если безопасное частичное решение невозможно.

Task не должна содержать алгоритмических веток, thresholds или defaults, привязанных к
конкретному устройству, спортсмену, маршруту либо номерам records.

## Термины и модель результата

### Composite failure region

Bounded-последовательность records внутри одного continuity domain, содержащая один или
несколько independent physical anomaly seeds и соседние компоненты, которые необходимо
рассмотреть совместно из-за unsafe immediate anchors или missing-position boundaries.

Наличие missing records само по себе не создаёт composite failure region. Нужен хотя бы
один независимый seed от существующего course-independent detector-а.

### Component

Максимальный внутренний участок одного типа:

- `POSITIONED` — records имеют координаты;
- `MISSING` — координаты отсутствуют.

Positioned component получает одно из machine-readable состояний либо их эквивалент:

- `PROVEN_CORRUPTED` — corruption доказана физическими признаками без course;
- `TAINTED` — component затронута независимыми impossible/suspicious transition
  evidence, но не целиком покрыта detected core; её reconstruction остаётся не выше
  `MEDIUM` и требует явного opt-in;
- `PLAUSIBLE` — движение физически правдоподобно, evidence ошибки нет;
- `UNKNOWN` — evidence недостаточно для предыдущих состояний.

`MISSING` описывает отсутствие наблюдения, а не доказанную corruption и не известный
маршрут движения.

### Detected core

Records, corruption которых установлена существующим или новым явно сформулированным
course-independent proof rule. Detected core может состоять из нескольких частей.

### Diagnostic region

Полная область совместного анализа между предложенными внешними anchors. Она может
включать proven-corrupted, plausible, unknown и missing components. Включение component
в diagnostic region не даёт права менять её coordinates.

### Reconstruction scope

Точный набор records, для которых candidate предлагает координаты. Он хранится отдельно
от detected core и diagnostic region и обязан указывать причину включения каждой
component.

### Stable outer anchors

Positioned records за пределами diagnostic region, имеющие требуемый независимый
NORMAL context. Их стабильность определяется без course.

## Сначала исследовать

До изменения production pipeline:

- описать, какие anomaly seeds могут начинать composite-region analysis;
- определить bounded adjacency rules для объединения abnormal transitions и
  missing-position runs в один diagnostic region;
- определить, когда plausible/unknown positioned component разрывает регион и когда
  лишь запрещает whole-region reconstruction;
- сравнить whole-region replacement, component-wise reconstruction и gap-only fill;
- сформулировать доказательство, необходимое для изменения positioned records;
- сформулировать отдельную policy для генерации координат там, где их не было;
- определить confidence matrix независимо для detection evidence и reconstruction;
- проверить поведение на нескольких synthetic morphology и как минимум одном real
  private fixture;
- оценить worst-case complexity и установить scan bounds до реализации.

Результаты исследования, выбранные proof rules и thresholds должны быть документированы
в этом task-файле либо в `docs/DECISIONS.md` до завершения реализации.

## Результат исследования и выбранная policy

Composite analysis запускается только для существующего `CorruptedInterval`, когда его
непосредственный before/after anchor не проходит course-independent NORMAL-context
gate. Границы расширяются существующим bounded mixed-region grouping: максимум
`mixed_region_search_max_records` records с каждой стороны, а evidence присоединяется
только через разрыв не больше `mixed_region_max_clean_gap_records + 1`. Continuity и
activity boundaries остаются жёсткими пределами.

Внутри diagnostic region строятся максимальные `POSITIONED`/`MISSING` components.
Positioned component классифицируется без course:

- полностью покрыта detected core — `PROVEN_CORRUPTED/HIGH`;
- касается `IMPOSSIBLE`/`SUSPICIOUS` transition — `TAINTED/MEDIUM`;
- содержит не меньше `anchor_stability_min_normal_transitions` внутренних `NORMAL`
  переходов и не имеет abnormal evidence — `PLAUSIBLE/HIGH`;
- иначе — `UNKNOWN/LOW`.

Region допускается к reconstruction planning только при stable outer anchors,
plausible direct outer bridge, наличии independent detected core, missing records и
нескольких components. Это разрешение построить и проверить hypothesis, а не право
изменить всю область. Course matching использует только внешние anchors; ambiguous или
physically implausible traversal остаётся unresolved.

Whole-region candidate сначала строится для проверки единой temporal hypothesis, после
чего update scope фильтруется component-wise. В scope входят `PROVEN_CORRUPTED`,
`TAINTED` и `MISSING`; `PLAUSIBLE` и `UNKNOWN` всегда сохраняются. Все переходы от
изменённых точек к сохранённым компонентам проверяются теми же physical limits. При
abnormal allocation planner пробует timestamp allocation; remaining impossible
connector отклоняет candidate. Итоговый confidence всегда не выше `MEDIUM`, поэтому
default `--min-confidence high` ничего не меняет.

Writer принимает точные непересекающиеся `reconstruction_scope_ranges`, а не требует
покрытия всего diagnostic interval. Missing coordinate можно записать lossless только
когда исходное FIT message definition уже содержит `position_lat/position_long` с
invalid value. Writer не расширяет FIT definitions; структурно отсутствующее поле даёт
явный отказ.

Алгоритм переиспользует существующие bounds и выполняет линейную component aggregation
внутри bounded окна. Новых route/device-specific thresholds и полного O(n²) поиска нет.

## Detection diagnostics

Detection stage не получает reference course и не строит маршрут.

Требуется:

- использовать results существующих local/classic/one-sided detectors как independent
  seeds, не меняя задним числом их reasons и confidence;
- выполнять bounded grouping только внутри одного continuity domain;
- строить ordered component list с boundaries, timestamps, record counts и position
  availability;
- для positioned components агрегировать impossible/suspicious/unknown transitions и
  другие разрешённые course-independent evidence;
- сохранять component state и reasons, не распространяя proof одного jump на весь
  соседний участок;
- проверять NORMAL context proposed outer anchors;
- рассчитывать direct outer bridge только как reachability diagnostic;
- сохранять rejected/partial regions с точными machine-readable reasons;
- не менять общий integrity status только из-за missing, plausible bridge или размера
  diagnostic region.

Stable outer anchors и plausible direct bridge необходимы для дальнейшего planning, но
не доказывают внутренний маршрут и не повышают component state до
`PROVEN_CORRUPTED`.

## Reconstruction policy

Reference course разрешён только после формирования course-independent component
diagnostics.

Reconstruction planner должен:

- использовать stable outer anchors либо иные anchors, безопасность которых доказана
  теми же общими правилами;
- проверять candidate count, direction, temporal order, course span и ambiguity;
- не использовать distance-to-course для изменения component state;
- предлагать updates для `PROVEN_CORRUPTED` positioned components;
- рассматривать missing-coordinate generation как отдельный тип update с отдельной
  причиной и confidence;
- сохранять `PLAUSIBLE` positioned components неизменными;
- сохранять `UNKNOWN` positioned components неизменными, пока независимый proof rule не
  разрешает обратное;
- уметь строить несколько component/gap candidates, если безопасный whole-region
  candidate потребовал бы изменения plausible/unknown movement;
- отказываться от всего или части reconstruction, если неизменяемые components нельзя
  физически непрерывно соединить с course candidate;
- применять существующую signal-allocation policy только после определения разрешённого
  scope;
- не изменять timestamps и независимые sensor fields;
- выполнять physical post-check всех внешних и внутренних connectors;
- отклонять candidate с новыми impossible transitions, temporal inversion,
  backtracking или несогласованной cumulative distance.

Reference course подтверждает только reconstruction hypothesis. Он не превращает
plausible/unknown coordinates в corrupted data.

## Confidence и применение

- confidence corruption определяется только detection evidence;
- reconstruction confidence учитывает anchor quality, course ambiguity, traversal
  plausibility, connectors и долю inferred coordinates;
- course proximity, красивый результат и private reference output не повышают
  confidence;
- candidate, содержащий сгенерированные missing coordinates, по умолчанию не может быть
  автоматически применён как `HIGH` без отдельно принятого общего proof rule;
- любой candidate ниже текущего default threshold остаётся explicit opt-in через
  `--min-confidence`;
- component candidates выбираются независимо, а отчёт перечисляет каждый
  `APPLIED`/`SKIPPED` результат.

Точная confidence matrix является результатом обязательного исследования. Она не может
подбираться так, чтобы один private fixture обязательно стал repairable.

## Console / JSON / HTML

Для каждого composite region отчёт показывает:

- diagnostic-region boundaries и stable outer anchors;
- ordered component table;
- тип, state, confidence и reasons каждой component;
- position/missing record counts и duration;
- abnormal-transition metrics для positioned components;
- anchor NORMAL-context и direct bridge metrics;
- detected core отдельно от diagnostic region;
- reconstruction scope отдельно для каждого candidate;
- число proposed updates для existing и missing coordinates;
- course projections, direction, span, ambiguity и connector metrics только в
  reconstruction section;
- `APPLIED`/`SKIPPED` и точные причины по каждому candidate;
- remaining missing, plausible и unknown components после repair.

HTML layers должны позволять визуально различить detected corruption, missing data,
plausible/unknown components, course, candidate и repaired geometry.

## Configuration и производительность

Все новые adjacency distances, record/time windows, stability requirements, limits и
tolerances находятся в configuration model.

Каждый параметр имеет:

- vendor-neutral имя;
- единицы измерения;
- документированный default;
- включение в verbose/JSON diagnostics;
- boundary tests.

Запрещены полный O(n²) поиск и значения, рассчитанные из одного private activity.
Расширенный анализ запускается только вокруг bounded anomaly seeds. Цель анализа
активности около 20 000 records остаётся менее 5 секунд на современном ноутбуке.

## Synthetic regressions

Минимальный набор общих morphology:

- несколько corrupted positioned components, разделённых short/long missing runs;
- corrupted component, затем plausible positioned movement и dropout;
- corrupted component, затем unknown positioned component;
- обычный tunnel/dropout без independent anomaly seed;
- остановка с missing coordinates;
- реальный off-course detour рядом с dropout;
- switchback рядом с anomaly seed;
- unsafe immediate anchors и stable outer anchors;
- continuity boundary внутри search window;
- несколько равноценных course spans;
- forward и reverse course traversal;
- physically plausible outer bridge, но implausible course traversal;
- safe gap-only reconstruction при неизменяемой positioned component;
- отказ, когда component-wise candidates нельзя физически соединить;
- candidate с impossible internal connector;
- missing/decreasing/reset recorded distance;
- bounded/performance fixture около 20 000 records.

False-positive fixtures не получают coordinate updates. Course не передаётся в
detection tests.

## Общие Acceptance Criteria

- модель не содержит vendor, route, athlete или record-index specific branches;
- composite region создаётся только вокруг independent course-free anomaly evidence;
- component boundaries и states стабильны и доступны в console/JSON/HTML;
- `PLAUSIBLE` positioned movement никогда не изменяется;
- `UNKNOWN` positioned movement не изменяется без дополнительного общего proof rule;
- missing-coordinate updates явно отличаются от replacement существующих coordinates;
- detected core, diagnostic region и reconstruction scope не смешиваются;
- ambiguous или physically invalid reconstruction остаётся unresolved;
- default application не становится менее консервативным;
- timestamps и независимые sensor data сохраняются;
- FIT writer/validate/diff объясняет все dependent-field changes;
- существующие classic, one-sided и safety regressions не меняют результаты;
- performance target соблюдён;
- все tests, Ruff format/check и mypy проходят;
- proof rules, confidence matrix и thresholds документированы.

## Private regression: Andromeda

Private Andromeda является одним acceptance fixture, а не спецификацией алгоритма.
Production code не знает приведённых ниже номеров records и не использует reference
fixed FIT для detection, boundary selection, confidence или course matching.

Наблюдаемая morphology fixture:

- diagnostic mixed region `8820..9580`;
- stable outer anchors `8819/9581`;
- positioned prefix `8820..8866` с abnormal transitions;
- missing run `8867..8878`;
- positioned fragment `8879..8892`;
- missing run `8893..9580`;
- существующий classic core `8841..8854`;
- unsafe immediate anchors `8840/8855`;
- всего 700 missing records, 5 impossible и 8 suspicious transitions;
- direct outer bridge около `994.1 м / 762 с = 1.30 м/с`.

Private acceptance проверяет:

- общий механизм представляет fixture как ordered components без специальных веток;
- unsafe immediate anchors не используются для reconstruction;
- stable outer anchors найдены course-independent способом;
- report перечисляет оба missing runs и evidence positioned components;
- default threshold оставляет unresolved/non-selected components неизменными;
- если общие правила дают однозначные безопасные component candidates, explicit
  confidence threshold позволяет их применить;
- если общих доказательств недостаточно, отчёт называет конкретное unsatisfied rule, а
  не подменяет его исключением для Andromeda;
- после любого применения нет новых impossible transitions и сохранены timestamps,
  altitude, speed, HR, cadence, power, temperature и unknown/developer data;
- ранее реализованные intervals не регрессируют.

`Andromeda_Taras_FIXED.fit` разрешён только как private visual/test oracle для оценки
результата после того, как общие правила и thresholds уже определены.

## Не делать

- проектировать алгоритм от конкретных номеров records или километровых отметок;
- требовать whole-region replacement ради непрерывной линии;
- считать missing data доказательством фактически пройденного пути;
- распространять corruption одного component на plausible/unknown соседей;
- использовать GPX course, reference fixed FIT, OSM или DEM в detection;
- повышать confidence по совпадению с course или private oracle;
- ослаблять classic/one-sided detector ради одного fixture;
- менять timestamps, altitude или независимые sensor fields;
- реализовывать Task 005C odometer-consistency detector;
- добавлять OSM/DEM routing, map matching, vendor API или web pipeline.
