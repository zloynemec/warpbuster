# Task 011 — Local GPX Gap Reconstruction

Статус: выполнена 2026-09-03.

Milestone: **M10**. Предыдущий этап: [Task 010](010-osm-graph-routing.md).
Следующий: **M11 / Task 012 — OSM Reconstruction Bridge**, только после acceptance
этой задачи. OSM package уже реализован; его подключение к repair пока не начинается.

## 1. Проблема и цель

В FIT необходимо различать три ситуации:

- координаты отсутствуют, но timestamps и остальные данные могут быть пригодны;
- координаты доказанно физически недостоверны;
- спортсмен действительно отклонился от курса, а движение физически правдоподобно.

Целевой порядок: независимо от GPX определить недостоверные координаты, логически
отбросить только их, собрать все получившиеся пустоты вместе с исходными missing и
для каждой отдельно попытаться подобрать путь по GPX. При отсутствии подходящего
пути пустота остаётся unresolved; будущий Task 012 сможет рассмотреть OSM.

В режиме `--fill-missing-from-course` пользователь явно выбирает реконструкцию по
имеющемуся курсу. Рабочее допущение этого этапа: там, где достоверных GPS координат
нет изначально либо они предварительно удалены по независимому corruption proof,
пытаемся восстановить движение вдоль GPX. Дополнительное внешнее подтверждение того,
что спортсмен прошёл каждый отсутствующий участок курса, не требуется. Для пустот
на границах активности это допущение включает соответствующие начало и конец курса.
Сам флаг разрешает такую привязку; отдельное подтверждение endpoints не вводится.

Это допущение реконструкции, а не новый источник corruption proof: оно не разрешает
удалять или притягивать к GPX сохранённое правдоподобное движение. Локальная привязка,
время, пригодные distance/speed данные и физическая правдоподобность по-прежнему
проверяются; допущение не отменяет отказ при противоречии или неоднозначном пути.

**Правдоподобный крюк в середине активности не может запрещать восстановление её
начала, если локальные данные начала, GPX и политика реконструкции не изменились.**

Цель — исправить архитектуру GPX reconstruction, а не ослабить thresholds ради одного
FIT. Найденный физически возможный путь является гипотезой реконструкции, а не
доказательством того, что спортсмен действительно бежал именно по нему.

### Где возникла неверная зависимость

На момент постановки задачи:

- `reconstruction/course.py::build_course_repair_plan` начинает с corrupted intervals;
  отдельный `missing.py::_endpoint_targets` рассматривает только prefix/suffix;
- `missing.py::_observed_alignment` выбирает один самый длинный positioned run и
  сравнивает всю его recorded distance с course span. Его отказ распространяется
  сразу на оба endpoint gaps, даже если причина находится далеко от них;
- `_endpoint_completion` использует начало/конец этого run как join anchor, хотя это
  может быть не ближайшая сохранённая точка к заполняемой пустоте;
- `course.py::_refine_one_sided_boundaries` расширяет границы замены до устойчивого
  GPX corridor. Поиск контекста для matching смешан с разрешением менять координаты;
- writer умеет применять replacements, но не имеет самостоятельной операции
  invalidation, когда плохие координаты доказаны, а подходящий путь не найден.

Task 011 заменяет эти правила. Завершённые [006B](006b-one-sided-gnss-failure-clusters.md),
[006C](006c-composite-gnss-failure-regions.md) и
[006D](006d-course-backed-missing-position-completion.md) остаются историей реализации,
но их прежние ограничения не являются требованиями совместимости нового алгоритма.

## 2. Scope и неизменяемые правила

Входит: общая coordinate mask, gap inventory, локальный GPX planner, раздельные
invalidation/reconstruction decisions, безопасное применение и console/JSON/HTML audit.
Поддерживаются исходные и возникшие после invalidation prefix/internal/suffix gaps.

Обязательные инварианты:

1. Integrity Detector, corruption proof и coordinate mask не принимают GPX/OSM/DEM.
   Расхождение с course, его длиной или DEM не доказывает corruption.
2. Исходная `ActivityData` и raw FIT остаются неизменными. Masked geometry — отдельное
   представление для planning, а не перезапись входных records.
3. Правдоподобные и не доказанные как испорченные positioned records сохраняются.
   `SUSPICIOUS`, `UNKNOWN` или нахождение внутри diagnostic region сами по себе
   не разрешают удаление координат.
4. Timestamps, порядок и количество records неизменны. Нельзя добавлять время для
   устранения невозможной скорости или создавать synthetic records между FIT records.
5. Все gaps планируются по одному исходному snapshot и mask. Сгенерированные
   координаты одного gap не становятся evidence/anchors для другого в том же проходе.
6. Решения локальны и независимы. Общий `SUSPICIOUS` status, другая unresolved пустота,
   удалённая ошибка или реальный крюк не являются глобальным veto.
7. Ни GPX matching, ни connectors, ни refinement не расширяют coordinate edit mask.

Не входит: OSM/Valhalla bridge, network/cache changes, DEM/altitude repair, vendor APIs,
новые detector heuristics, реализация отложенного Task 005C, автоматическое определение
независимого источника FIT distance, новый HTML-шаблон или изменение исходного FIT.

## 3. Coordinate validity и единый список пустот

### 3.1. Отдельное решение об invalidation

Добавить typed course-independent результат с точными record IDs, исходным состоянием
position fields, detector evidence/reasons, confidence и решением apply/skip.
Минимальные состояния геометрии для planner:

- `ORIGINAL_MISSING` — исходно нет пригодной пары lat/lon;
- `INVALIDATED` — удаление имеющихся координат разрешено независимым proof и политикой;
- `PRESERVED` — координаты остаются как есть; отдельно указывается пригодность в anchors.

Для partial coordinate pair сохранять field-level provenance: допустимо заполнить
неполную пару, но нельзя молча потерять факт наличия одного из исходных значений.

Proof должен определять, какие records недостоверны. Один impossible transition не
разрешает произвольно удалить оба его конца. Доказанный spoofing island, напротив,
может включать локально плавные точки: не требуется невозможная скорость у каждой
точки, если независимые граничные доказательства относятся ко всему island.

Для composite regions маска строится component-wise по доказательствам. `TAINTED`
не является самостоятельным разрешением стереть весь компонент. Если текущая модель
не позволяет вывести точный безопасный scope, оставить coordinates preserved и
показать недостаточность proof; не компенсировать её близостью GPX.

Confidence удаления и confidence предлагаемого пути — отдельные величины.
Недостаточная уверенность в пути не отменяет уже разрешённую invalidation.
Если invalidation не разрешена, соответствующие существующие координаты не могут
быть заменены GPX; потенциально недостоверные records не становятся trusted anchors.

### 3.2. ReconstructionGap

Построить inventory одним проходом по masked geometry, до обращения к course.
В контракте каждого gap предусмотреть:

- стабильный ID внутри исходной активности, точный набор records и timestamps;
- отдельные признаки положения `PREFIX / INTERNAL / SUFFIX` и происхождения
  `ORIGINAL_MISSING / INVALIDATED / MIXED`;
- continuity segment и provenance каждой части;
- непосредственные сохранённые observations слева/справа, если они существуют;
- отдельный локальный alignment context и результаты проверки доверия к anchors;
- независимые start/finish constraints, если они имеются; они не являются обязательным
  условием course-backed режима. Принятые по GPX endpoint assumptions сохраняются
  отдельно в candidate provenance, не в course-independent inventory;
- diagnostics eligibility, limits и причины unresolved независимо от provider.

Смежные original missing и invalidated records образуют одну смешанную пустоту.
Сохранённый positioned component, даже короткий или `UNKNOWN`, разделяет edit scopes.
Его нельзя поглотить ради удобного большого route. Существующую disjoint-модель
composite region можно сохранить как группировку нескольких gaps, но не как право
переписать весь внешний диапазон.

Не пересекать явно заданные continuity boundaries FIT/нормализованной модели.
Активность без единого пригодного observed anchor должна получить явный unresolved
результат, а не исчезнуть из отчёта как `NOT_NEEDED`. Полная реконструкция такой
активности только по геометрии курса без observed anchors в этот этап не входит.

### 3.3. Минимальный provider-neutral контракт

Общий gap, coordinate update и решение apply/skip не должны требовать `course_path`
или `course_distance_m`. GPX segment, chainage, direction и source hash относятся
к GPX candidate provenance. Нужен только контракт для текущего GPX provider и будущего
Task 012, без registry framework, OSM imports или фиктивной реализации routing.

## 4. Локальное сопоставление с GPX

### 4.1. Scope, join anchors и context — разные сущности

Для каждого gap отдельно:

1. Зафиксировать edit mask и реальные соседние preserved records.
2. Проверить локальную устойчивость anchors без course. Не считать каждую сохранённую
   точку автоматически доверенной; плохая ближайшая граница может блокировать этот gap.
3. Искать matching evidence в ограниченном окне наружу от этих границ, не через другую
   пустоту/continuity break. Расширять окно только по детерминированному правилу и
   до заданного лимита, а не до получения желаемого ответа.
4. Найти допустимые проекции локальной последовательности на GPX и оценить варианты
   branch/direction с учётом порядка точек, времени, локальной длины и ошибок привязки.
5. Построить путь и connectors к фактическим join anchors, распределить координаты,
   проверить все новые переходы и вернуть candidate либо конкретную локальную причину.

Более дальние устойчивые точки могут помочь определить course branch, но не заменяют
ближайшую сохранённую точку при стыковке. Если соединение с ней небезопасно, отказ
относится к этой пустоте. Нельзя «перепрыгнуть» сохранённые observations.

Прежнее one-sided corridor refinement допустимо только как поиск matching context.
Его `refined_start/end` больше не определяют write scope. Если после этого для старого
fixture остаётся недоказанная drift-геометрия, она сохраняется с диагностикой; нельзя
поднять detector confidence или удалить её только ради прежнего golden output.

### 4.2. Локальные критерии, а не глобальная привязка всей активности

- Не выбирать longest positioned run как обязательный alignment для всех gaps.
- Не сравнивать целиком FIT total distance или длинный удалённый run с GPX distance.
  Для consistency использовать только явно указанные локальные дельты; накопленный
  offset от предшествующего настоящего крюка не должен влиять на результат.
- Оценивать gap path отдельно от observed context: длина внутри пропуска и длина
  контекста не смешиваются. Надёжность distance/speed и критерии их пригодности
  показываются явно; неизвестное происхождение поля не объявляется независимым.
- Направление и GPX branch определяются локально. При петлях, out-and-back и
  самопересечениях близость одной точки не означает единственность пути.
- Кандидат лежит в одном непрерывном GPX segment; разрывы между segments не сшиваются.
  Несколько подходящих segments/branches требуют явного ambiguity handling.
- Deduplication эквивалентных проекций не должна склеивать разные обходы петли.
  Если bounded search обрезан и единственность не доказана, нельзя выдавать `unique`.
- Правдоподобный observed off-course участок сохраняется. Если именно локальные
  anchors не позволяют GPX match, unresolved получает только связанный с ними gap.

### 4.3. Prefix и suffix

У каждого endpoint gap собственные context, candidate и решение. Успех prefix не
зависит от успеха suffix и наоборот. Join anchor prefix — первая фактическая сохранённая
точка после него; для suffix — последняя перед ним, а не границы дальнего run.

При `--fill-missing-from-course` внешний endpoint берётся из GPX как часть явно
выбранной политики, без отдельной пользовательской декларации:

- для prefix строится путь от начала курса в выбранном направлении до первой
  сохранённой позиции, через её локальную привязку к GPX;
- для suffix — от последней сохранённой позиции через локальную привязку до конца
  курса в выбранном направлении;
- правило одинаково для исходно missing и предварительно invalidated endpoint gaps.

Направление определяется локальным matching; для reverse traversal начало и конец
соответствуют обратному обходу GPX. Разрывы GPX segments не сшиваются; нельзя заменить
endpoint всего курса ближайшим концом произвольного segment, чтобы получить candidate.
Неоднозначное направление/branch или недопустимая стыковка дают локальный отказ.

Отсутствие внешнего подтверждения старта/финиша само по себе не является причиной отказа.
В audit указывается `endpoint_source=course_assumption`, а не подтверждённый факт.
Это допущение не отменяет time/distance checks, confidence и физическую проверку.
Без GPX действует `no_course`; способ задания границ для будущего OSM-only режима
остаётся в Task 012 и не ограничивает нынешний course-backed режим.

## 5. Path confidence, allocation и физическая проверка

Разделить evidence о corruption, выборе пути и распределении движения во времени.
Для первоначально missing coordinates сохранить максимум `MEDIUM`; автоматическое
применение по default `HIGH` не включать. Смешанный gap не получает `HIGH` лишь потому,
что часть его records была повреждена с высокой уверенностью. `LOW/UNKNOWN` не применять.
Для prefix/suffix с `endpoint_source=course_assumption` также максимум `MEDIUM`,
независимо от происхождения пустоты: допущение не повышает достоверность пути до `HIGH`.

Allocation использует исходные timestamps и квалифицированные локальные сигналы:

1. пригодные локальные recorded-distance deltas;
2. пригодную integrated recorded speed;
3. elapsed time как явно помеченную estimated interpolation.

Предыдущий record-order fallback не применять, если он скрывает отсутствие пригодного
времени. Distance и speed квалифицируются независимо, без GPX; используются существующие
физические пределы activity profile. Non-monotonic/zero/invalid signal диагностируется.
Несогласие distance с длиной пути не запрещает проверить пригодный speed; согласованный
альтернативный источник может дать allocation. Однако time-only fallback не должен
обходить противоречие правдоподобных измерений (включая нулевое движение) с путём.
Допуск длины: `max(signal_distance_absolute_tolerance_m, path_length * 0.15)`;
абсолютная часть учитывает погрешность коротких участков, не отменяя физические проверки.
Сигналы неизвестного происхождения могут
помогать allocation, но не служат независимым подтверждением corruption или `HIGH`.

Проверять среднюю скорость по пути, connectors, все новые внутренние transitions и
стыки с **непосредственными** сохранёнными соседями каждого edit scope. Учитывать
исходные continuity/timer данные, не растягивать clocks и не переносить движение между
отдельными gaps. При недостающих временных данных — явный unresolved, без выдуманного
расписания движения.

В реализации известные FIT timer `stop/stop_all/stop_disable/stop_disable_all` до
соответствующего `start` запрещают allocation в пересекающей паузу пустоте:
`timing_unusable, timer_pause`. Остальные пустоты независимы. Перераспределение пути
между active/paused windows не реализуется; неизвестные типы событий не трактуются
как stop/start и не меняются.

Перед записью проверить совокупность выбранных updates в masked activity. Новая
невозможная геометрия блокирует затронутый candidate, но существующая несвязанная
аномалия не отменяет остальные. Общая structural/CRC/preservation validation FIT
остаётся обязательной для всего output и может заблокировать его публикацию.

## 6. FIT writer и политика неполного результата

Writer должен принимать два независимых вида операции:

- invalidation разрешённых position fields до FIT invalid value;
- replacement координат выбранным reconstruction candidate.

Запись выполняется одним atomic pass по исходным FIT frames, после selection и
preflight. Для одного record финальный replacement может перекрывать planned
invalidation; audit сохраняет обе стадии, но coordinate diff отражает конечные bytes.
Если candidate отклонён, разрешённая invalidation остаётся: известные плохие координаты
не возвращаются в очищенный output из-за отсутствия найденного пути.

Если разрешены только invalidations, output всё равно создаётся как частичный cleaned
FIT. Если нет никаких разрешённых изменений, output не создаётся и CLI объясняет no-op.
Originally missing без candidate остаются missing. Сохранённые uncertainty records
остаются исходными, а не маскируются в output без разрешения.

Lossless scope:

- менять только разрешённые coordinate fields и необходимые поля с известной
  coordinate-dependent семантикой; timestamps/sensors/altitude не менять;
- сохранять unknown/developer fields, definitions и прочие messages;
- не добавлять отсутствующие FIT fields или менять message schema ради записи;
  unpatchable scope имеет отдельный отказ до публикации output;
- не превращать FIT → GPX → FIT; не перезаписывать исходный FIT даже с `--overwrite`;
- сохранять существующие raw-byte validation, semantic diff, CRC и atomic output.

### Distance и summaries

Общая длина FIT не обязана равняться длине GPX: реальные крюки остаются частью активности.
Правдоподобные recorded streams нельзя нормализовать под course.

- Решение о коррекции distance не выводится из origin пустоты. Для original-missing,
  invalidated и mixed действует одна локальная политика: правдоподобный stream сохраняется;
  доказанно невозможный — корректируется в успешно восстановленном scope с отдельным diff.
  Доказательство невозможности основано на FIT delta/time и activity profile, не на GPX.
  Неизвестное происхождение сигнала не объявляется независимым измерением.
- Для коррекции нужны полные, конечные, неубывающие локальные distance значения:
  partial/non-monotonic streams сохраняются с uncertainty, reset semantics не угадываются.
  Если профиль не задаёт абсолютный физический предел, автоматического доказательства
  невозможной distance по этому критерию нет.
- Unresolved geometry в других местах не запрещает локальную коррекцию. Вне восстановленного
  scope сохраняются исходные приращения; downstream cumulative distance и поддержанные
  lap/session totals/average speed меняются только на накопленную поправку. Не применять
  глобальный пересчёт всей активности по геометрии и не подгонять total под GPX.
- Record speed, sensors/altitude и поля с неизвестной семантикой сохраняются. Незавершённая
  геометрия или сохранённый противоречивый distance дают `distance_quality=uncertain`.
  Состояние distance отделено от состояния геометрии: `partially_corrected` допустимо.
- После записи, до atomic publication, повторно декодировать временный FIT и проверить
  фактические координаты, стыки, timestamps, cumulative/summary patches. Валидный CRC
  сам по себе не доказывает, что исправления действительно попали в файл.
- Нельзя соединить пустоту хордой, объявить её длину нулевой или интерполировать
  distance только для того, чтобы существующий writer смог закончить расчёт.

## 7. CLI и совместимость

Реализованный CLI-контракт:

- `repair --course` становится optional: без GPX доступны gap inventory и разрешённая
  invalidation; reconstruction gaps получают `no_course`, routing не запускается.
- `--fill-missing-from-course` включает единое course-backed заполнение всех пустот:
  исходных missing, предварительно invalidated и mixed, включая prefix/internal/suffix.
  Сам флаг разрешает принять GPX как предполагаемый путь внутри пустот и использовать
  его endpoints для начала/конца активности. Отдельный флаг подтверждения не требуется.
  Без этого opt-in сохраняется обычный repair доказанных corrupted scopes, но исходные
  missing и endpoint completion по допущению курса не включаются автоматически.
  Inventory и причины skip показываются в обоих режимах.
- `--min-confidence` сохраняет назначение порога reconstruction и default `HIGH`.
  Ни значение `low`, ни наличие GPX не разрешают применение `LOW/UNKNOWN` candidates.
- Добавить отдельный `--min-invalidation-confidence {high,medium}`, default `high`.
  Он ограничивает только independently proven coordinate invalidation. Значение
  `medium` — явный opt-in, не разрешение удалять обычные suspicious/unknown records.
  Таким образом, снижение reconstruction confidence не снижает порог удаления.
- Без GPX `--fill-missing-from-course` даёт понятную ошибку аргументов.
- `--dry-run` строит полный план, включая invalidations и skipped operations, без FIT
  write. `--output`, `--html [PATH]`, их defaults и защита `--overwrite` сохраняются.
- `analyze` остаётся read-only анализом исходного FIT: GPX не влияет на detection,
  координаты для его карты не подменяются planned/cleaned geometry.

Сохранить endpoint opt-in из 006D: `--fill-missing-from-course` достаточно для принятия
course endpoints как допущения. Обновить README/help и CLI tests для единого заполнения
пустот и явного отображения этого допущения; не требовать нового подтверждения границ.
Для прежних `MEDIUM` corruption repairs также явно документировать необходимость
обоих разрешений: `--min-invalidation-confidence medium --min-confidence medium`.
Это не требуется для чистого missing completion без удаления исходных координат.

Пример вызова course-backed режима:

```bash
warpbuster repair activity.fit --course route.gpx \
  --fill-missing-from-course \
  --min-confidence medium --dry-run --html
```

Единая обработка всех видов пустот по этому вызову реализована в Task 011.

## 8. Отчёт и диагностика

Использовать существующий `report/assets/report.html`, не создавать отдельный шаблон.
Console/JSON/HTML должны разделять physical integrity, coordinate coverage и результат
reconstruction. Missing target не становится detected corrupted interval.

Для каждого gap показывать:

- стабильный номер на карте и соответствующую строку таблицы, FIT records/time scope;
- original missing / invalidated counts и detector proof для удаления;
- actual join anchors, context range и course/endpoint assumptions с указанием
  `--fill-missing-from-course` как основания выбранной политики;
- GPX segment/branch/direction, geometry, connectors, локальные длины и signal quality;
- confidence invalidation и path отдельно, allocation method и его ограничения;
- planned/applied/skipped/unresolved, причины и provider provenance;
- окончательный coordinate/FIT diff и distance policy в write-mode.

Сводка раздельно считает originally missing, invalidated, filled и unresolved records;
происхождение не смешивается с результатом. Для mixed gaps не удваивать число updates.
Никаких сплошных линий через unresolved holes на карте. Существующие беговые сводки,
графики темпа и набора сохраняются; uncertain distance явно влияет на подписи, а не
выдаётся за достоверную исправленную статистику.

Минимальные machine-readable причины (допустима согласованная адаптация существующих
enum names): `no_course`, `missing_completion_disabled`, `insufficient_corruption_proof`,
`invalidation_below_threshold`, `no_trusted_local_anchor`,
`local_course_match_not_found`, `local_course_match_ambiguous`,
`local_distance_inconsistent`, `timing_unusable`, `candidate_transition_implausible`,
`continuity_break`, `search_limit_reached`, `position_fields_unpatchable`.
Отказ содержит конкретный gap и использованные локальные records, а не общую причину
о несоответствии всей активности GPX.

## 9. Configuration и производительность

Все thresholds живут в typed configuration, а не в planner branches. Для каждого
нужны имя, units, комментарий, default, validation и boundary tests. `IntegrityConfig`
не перенастраивается ради GPX acceptance.

Базовая политика для реализации:

- новые `local_alignment_max_context_records=300` и
  `local_alignment_max_context_seconds=300.0` ограничивают поиск на каждую сторону;
  останавливаемся по первому достигнутому лимиту. Это caps, не обязательная длина окна;
- использовать ближайший достаточный stable context, а не всегда все 300 records;
  endpoint context требует minimum 30 positioned records; internal context с двумя
  anchors требует по 15 NORMAL transitions (16 records) с каждой стороны. Это
  сохраняет прежний двухсторонний anchor минимум и не переносит односторонний
  endpoint минимум на внутренние пустоты; оба граничных случая покрыты tests;
- сохранить локальное relative distance tolerance `0.15`, anchor tolerance `75 m`,
  максимум `32` проекции на anchor и имеющиеся правила ambiguity/deduplication;
  не расширять tolerance до получения ответа на private fixture;
- добавить `signal_distance_absolute_tolerance_m=3.0` — положительный конечный допуск
  в метрах для коротких gap paths; проверять обе стороны точной границы в synthetic tests;
- для observed context учитывать геометрическую погрешность проекции:
  `abs(observed_length - projected_span) <= observed_length * 0.15 +
  first_projection_error + last_projection_error`. Все промежуточные наблюдения
  дополнительно должны укладываться в anchor tolerance. Погрешность, локальные длины
  и источник observed progression сохраняются в `alignment_contexts` provenance.
  Проверка длины самого gap использует `max(3 m, length * 0.15)` без добавления ошибок
  проекции контекста. Короткий
  контекст с поперечным смещением покрыт отдельным synthetic regression;
- существующие missing completion bounds `10 m/s` для среднего пути/новых переходов
  и `50 000 records` на gap перенести с endpoints на все original-missing candidates;
  это reconstruction safety limits, не detector thresholds;
- общий bound `100` reconstruction targets считать по unified gaps; превышение
  не должно скрывать остальные gaps из inventory, они получают limit diagnostics.
- `local_alignment_max_path_evaluations=128` ограничивает число полных allocations
  на один gap по всем context windows. Повторные варианты с теми же anchors/direction
  берутся из cache; недопроверенные альтернативы дают `search_limit_reached`, не
  `unique`. Порог, его default и границы проверены tests.

Изменения этих defaults возможны только с объяснением и synthetic boundary tests,
не как скрытая подгонка под конкретную активность. Неиспользуемые global-alignment и write-refinement
настройки удалить либо явно deprecated; обновить `test_reconstruction_config.py`.

Inventory — линейный проход; reuse transition lookup и course index. Не делать полный
FIT×FIT reachability или неограниченный перебор course paths. Candidate search,
context и output bounded; truncation всегда аудируется. Проверить synthetic workload
~20 000 records и много локальных gaps, отдельно измерить normalize/detect/plan/report.
Сохранить существующий performance contract detector (<5 секунд на современном
ноутбуке); зафиксировать окружение и baseline планировщика без выдачи времени HTML/I/O
за время detector.

## 10. Обязательные регрессии

### Synthetic / публичные

1. **Locality:** один и тот же gap в чистой активности и с удалённым реальным крюком
   даёт одинаковые candidate geometry, confidence и decision при неизменных local
   records/context/course/config. Проверить также offset cumulative distance после
   предшествующего крюка: локальные deltas те же, результат не меняется.
2. Prefix и suffix имеют независимые success/refusal. Несколько positioned runs;
   самый длинный далеко от gap. Join проверяется именно с первым/последним реальным
   соседом, а не с alignment run. При включённом `--fill-missing-from-course` course
   endpoints используются без дополнительного подтверждения, для original-missing
   и invalidated gaps, с явным assumption в audit; без флага такое completion выключено.
3. Internal original-missing gap появляется в inventory и получает GPX attempt без
   corrupted interval. Без opt-in — видимый skip, а не `NOT_NEEDED`.
4. Proven spike/island превращается в gap; original missing + invalidated образуют
   mixed gap. Impossible edge без доказанного виновного scope не удаляет оба конца.
5. `PLAUSIBLE/UNKNOWN` components сохраняются и разделяют edit scopes; `TAINTED`
   без proof не стирается. Course-corridor refinement не расширяет mask.
6. Отсутствие, замена или разворот GPX не меняют detector/coordinate invalidation.
   Unrelated suspicious/corrupted участок не запрещает безопасный local candidate.
7. Loops, одинаково близкие разные branches, forward/reverse traversal, direction
   ambiguity, multiple segments и off-course anchors дают локальные объяснимые
   результаты. Course endpoints не подменяются концами промежуточных segments;
   отсутствие внешнего подтверждения endpoints не служит причиной отказа.
8. Context/candidate/work limits, incomplete search, timestamp problems, zero/reset/
   invalid distance, speed fallback и plausible/impossible connectors покрыты tests.
9. Generated coordinates не используются как новые anchors; порядок planning gaps
   не меняет semantic result. Все новые boundary transitions проверяются на composed
   geometry, исходные несвязанные diagnostics не исчезают и не служат общим veto.
10. Writer: invalidation-only, replacement, partial filled/unresolved, mixed confidence,
    unpatchable fields, no-op, CRC/atomic output и default no-overwrite. Все timestamps,
    sensors, unknown/developer fields и preserved positioned records неизменны.
11. Локальная коррекция доказанно невозможного distance в заполненном scope всех origins,
    независимо от других unresolved gaps; вне scope сохраняются приращения. No-chord/no-zero
    policy и отсутствие подгонки total distance под GPX с настоящим крюком. Короткие gaps,
    qualified speed fallback, запрет time-only override правдоподобного конфликта и
    read-back refusal при пропущенном metric patch покрыты synthetic tests.
12. CLI migration/defaults и раздельные confidence policies; analyze остаётся на
    original FIT. Console/JSON/HTML согласованы по IDs, counts, reasons и diff.

### Универсальный сквозной acceptance

Создать synthetic FIT/GPX fixtures с параметризуемыми количеством, длиной и положением
пустот. Сценарии должны включать prefix, несколько internal gaps и suffix, реальные
off-course участки, удалённый от проверяемой пустоты правдоподобный крюк, а также
пустоты после доказанной invalidation. Имена файлов, конкретные records, километраж
или география пользовательской активности не задают требования к алгоритму.

При `--fill-missing-from-course` и разрешённом пороге применения:

- все пустоты видимы отдельно с provenance и локальными решениями;
- однозначные физически допустимые prefix/internal/suffix candidates заполняются
  по GPX; для prefix/suffix достаточно opt-in режима, без дополнительных деклараций;
- после восстановления стыки с непосредственными сохранёнными observations проходят
  проверку, а timestamps и остальные protected FIT fields не меняются;
- настоящий крюк и прочие сохранённые observations не меняются и не блокируют
  независимые gaps; unresolved одного участка не отменяет успешные другие;
- неоднозначный или физически недопустимый путь остаётся unresolved по локальной
  причине, без требования любой ценой заполнить всю активность;
- output имеет valid CRC и только разрешённый diff; source FIT неизменен.

### Дополнительные private regressions

Повторить доступные private regressions как дополнительную проверку, не заменяющую
synthetic acceptance. Файлы остаются в ignored `tests/private/` и не коммитятся.
Отсутствующие fixtures указывать как skipped, не как passed. Если старый golden требует
course-only изменений за пределами доказанной mask, обновить ожидание с объяснением —
не ослаблять инвариант. Ни один конкретный private track не является условием
постановки задачи или источником production thresholds.

## 11. План реализации и затрагиваемые файлы

Выполнять последовательно внутри Task 011; это не разрешение начинать Task 012:

1. Ввести proof-backed mask, unified gaps и минимальные provider-neutral models;
   добавить locality/scope tests, демонстрирующие текущий дефект.
2. Перевести GPX matching на локальные anchors/context для всех gap kinds, исключить
   расширение write scope через GPX, добавить allocation и actual-boundary validation.
3. Разделить invalidation/reconstruction selection, обновить writer/distance policy,
   CLI и общий report; прогнать synthetic/private acceptance и обновить документацию.

Ожидаемые области изменений при реализации:

- `src/warpbuster/models/reconstruction.py`, `src/warpbuster/config.py`;
- `src/warpbuster/reconstruction/`: новые mask/gap helpers, `course.py`, `missing.py`,
  `safety.py`, `orchestration.py`, `selection.py`, публичные exports;
- `src/warpbuster/fit/writer.py` и необходимые FIT diff/validation models;
- `src/warpbuster/cli.py`, `src/warpbuster/report/{repair,html}.py`, общий HTML asset;
- новые `tests/test_reconstruction_gaps.py`, `tests/test_local_course_matching.py`,
  `tests/test_private_local_gpx_reconstruction.py` и существующие reconstruction,
  missing/composite/one-sided, config, writer, CLI/report tests;
- README, roadmap/milestones, описание заменённых правил в 006B/006D.

Detector algorithms и packages OSM Manager/routing не меняются в рамках этой задачи.
Если выяснится необходимость нового corruption proof, оформить отдельную задачу;
не скрывать его внутри GPX matching.

## 12. Acceptance criteria / Definition of Done

- [x] Course-independent mask и единый inventory охватывают все missing/invalidated
      prefix/internal/suffix gaps, не поглощая preserved components.
- [x] Каждый GPX candidate использует свои actual anchors и bounded context; synthetic
      distant-detour invariance и независимость prefix/suffix доказаны тестами.
- [x] Course/коридор не меняет detection, invalidation proof или write scope.
- [x] Clean internal gaps обрабатываются без fabricated corrupted intervals;
      ambiguity, physical contradictions и search limits дают явные локальные отказы.
- [x] `--fill-missing-from-course` разрешает course-backed заполнение original-missing,
      invalidated и mixed gaps; для prefix/suffix принимаются course endpoints без
      дополнительных подтверждений, с явным assumption в audit и максимум `MEDIUM`.
- [x] Invalidation и reconstruction имеют отдельные confidence/selection; unresolved
      плохие координаты можно удалить без replacement, сохранив остальные FIT данные.
- [x] Все новые transitions/стыки безопасны; timestamps и preserved observations
      неизменны; partial output не подгоняет distance под GPX и не скрывает uncertainty.
- [x] CLI migration/defaults/overwrite/dry-run документированы и протестированы;
      analyze остаётся read-only, reports используют один шаблон и показывают FIT diff.
- [x] Synthetic tests и универсальный сквозной acceptance пройдены без зависимости
      от конкретного private track; доступные private regressions пройдены либо
      отсутствие fixture явно указано.
- [x] Полный Core test/lint/type suite зелёный; performance baseline записан;
      OSM packages не изменены, новые зависимости на routing/DEM не добавлены.
- [x] Только после этого Task 011 отмечается выполненной и разрешается начало Task 012.

Команды проверки после реализации из корня репозитория:

```bash
.venv/bin/pytest tests/test_reconstruction_gaps.py tests/test_local_course_matching.py
.venv/bin/pytest tests/test_private_local_gpx_reconstruction.py -v
.venv/bin/pytest tests
.venv/bin/ruff check src tests
.venv/bin/ruff format --check src tests
.venv/bin/mypy src
git diff --check
```

Отдельный воспроизводимый замер:

```bash
.venv/bin/pytest tests/test_local_reconstruction_performance.py -q -s
```

## 13. Implementation report — 2026-09-03

Введены `gaps.py`, `local.py`, provider-neutral gap/update models и GPX provenance.
Прежние `course.py` и `missing.py` стали compatibility wrappers одного planner;
глобальная привязка и course-driven write refinement удалены, не оставлены вторым
обходным алгоритмом. Legacy plans без полной coordinate mask writer больше не принимает.
Старые настройки corridor/global alignment явно deprecated в config docstring.

Writer выполняет invalidation/replacement одним raw-byte pass, проверяет исходный
snapshot, mask, actual joins после FIT quantization, CRC и semantic diff; source FIT
и GPX защищены и от совпадения с HTML path при `--overwrite`. Invalidation-only и
partial output сохраняют остальные поля, unresolved geometry не получает выдуманной
дистанции. CLI, JSON и общий HTML показывают тот же inventory и G-номера.

Синтетические тесты: locality и cumulative offset, разные размеры/позиции всех видов
gaps вместе с настоящим крюком, отдельные пороги, immutable proof scopes, continuity,
loops/reverse/segments/ambiguity/search caps, timestamp/timer failures, signal fallback,
partial pairs/unpatchable fields, CRC/atomic/no-overwrite и HTML/JSON agreement.
Private goldens, требовавшие расширять mask только по GPX, заменены проверкой точного
proof scope и preservation; это изменение ожидаемого поведения, не ослабление detector.

Baseline на macOS arm64, Python 3.14.7, 20 000 records / 11 gaps, без генерации fixture:
normalize **0.591 s**, detect **0.230 s**, GPX plan **0.311 s**, HTML **0.159 s**.
Время зависит от окружения; сложные неоднозначные courses дополнительно ограничены
context/candidate/target/path-evaluation caps. Detector contract остаётся <5 s.

Ограничения: source distance/speed не объявляются независимыми; полная активность без
observed anchors, ambiguous/local mismatch, unpatchable fields и gaps через timer pause
остаются unresolved. OSM bridge, DEM, detector heuristics и новые внешние зависимости
не добавлены. На первоначальном этапе ручной визуальный smoke-test не выполнен: Browser заблокировал локальный
`file://` URL. HTML/JSON payload и синтаксис inline JavaScript проверяются локально;
обходить браузерный запрет не предпринималось.

Первоначальный Core suite: **280 passed, 5 skipped** (67.39 s); skips — отсутствующие
private fixtures прежних этапов. Доступные private regressions выполнены. `ruff check`,
`ruff format --check`, `mypy src` и `git diff --check` — без ошибок. Task 011 acceptance
проверена в первоначальном объёме; выявленные позже дефекты согласованности исправляются
в рамках той же Task 011, без перехода к routing.

### Коррекция согласованности геометрии и метрик — 2026-09-03

Выявленная общая проблема: частичное исправление координат ошибочно считалось достаточным,
хотя исходный cumulative distance мог сохранять физически невозможные скачки. Политика
метрик зависела от origin пустоты и наличия других unresolved gaps, а ранний отказ по
distance мешал проверить пригодный speed. Визуализация дополнительно смешивала сохранённую
и восстановленную геометрию и не отделяла исправленные координаты от неисправленных метрик.

Исправления:

- отдельная course-independent квалификация distance/speed и последовательная проверка
  пригодных источников; time-only не обходит правдоподобный конфликт;
- абсолютный допуск коротких paths в конфигурации с тестами точных границ;
- локальная коррекция доказанно невозможного distance для всех origins, без глобального
  veto от других gaps; исходные приращения вне восстановленного scope сохраняются;
- read-back validation фактического временного FIT до atomic publication;
- единый HTML разделяет input diagnostics, output distance, gap geometry и metric status,
  сохранённые/reconstructed линии; неопределённость дистанции указана в сводке и
  подсказках километровых меток, без дополнительных знаков на самих метках.

Публичные синтетические регрессии: все три origins с другими unresolved gaps и без них,
short-gap speed fallback, сохранение правдоподобного distance, отказ при конфликте обоих
сигналов, zero/reset/unavailable cases, границы физических/абсолютных/относительных порогов,
отказ публикации при пропущенном metric patch и согласованность JSON/HTML.
Browser smoke-test по локальному HTTP подтвердил отображение сводки, G-таблицы и стыка
на карте; исходные FIT/GPX сервером не раздаются. Detector и routing не изменялись.

Проверка после коррекции: **334 passed, 6 skipped** (77.71 s), `ruff check src tests scripts`,
`ruff format --check src tests scripts`, `mypy src`, `git diff --check` — без ошибок.
Критерии этой коррекции выполнены. Это не обещание полного восстановления любой активности:
неоднозначные пути, недостаточные anchors, правдоподобные конфликты и reset/partial distance
по-прежнему требуют явной uncertainty, а не принудительной подгонки под курс.
