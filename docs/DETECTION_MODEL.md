# GPS/GNSS Integrity Detection Model

## 1. Что именно мы детектируем

WarpBuster не определяет, «правильно ли человек бежал по маршруту».

Он определяет:

> Можно ли объяснить записанную последовательность координат физически непрерывным движением человека?

## 2. Базовые сущности

### Transition

Переход между двумя observations `Pi → Pj`.

Характеристики:
- `dt`;
- geodesic distance;
- apparent speed;
- optional heading change;
- optional vertical change.

### Impossible transition

Переход, который с консервативным запасом нельзя объяснить движением спортсмена.

### Spoofing island

Непрерывный блок observations, ограниченный невозможным входом и невозможным выходом, при этом trusted point before → trusted point after образуют физически правдоподобный bridge.

### One-sided GNSS failure cluster

Bounded interval с impossible entry, где exit transition скрыт missing-position run.
Один jump недостаточен. Для `MEDIUM` interval одновременно требуются:

- adjacent impossible entry;
- missing-position evidence и missing-terminated boundary;
- configurable consecutive `NORMAL` context снаружи обоих anchors;
- plausible direct bridge между anchors;
- каждый positioned-компонент внутри interval затронут `IMPOSSIBLE`/`SUSPICIOUS`
  evidence.

Если любое условие не выполнено, остаётся `LOW` unresolved diagnostic. Course, OSM,
reference fixed FIT и distance-to-course не участвуют ни в proof, ни в boundaries.

### Vertical warning

Running profile выполняет отдельную course-independent проверку altitude rate:

- sustained: не менее 3 последовательных transitions с `|vertical speed| >= 4 м/с`
  и `|delta altitude| >= 4 м` на transition;
- single extreme: `|vertical speed| >= 10 м/с` и `|delta altitude| >= 4 м`.

Пороги находятся в `IntegrityConfig`. Warning не является доказательством порчи
coordinates, потому что barometric/altitude sensor может ошибиться независимо от GNSS.
Он не меняет integrity status и не создаёт repair interval.

## 3. Почему локального despike недостаточно

Пример:

```text
A
 \
  X1─X2─X3─...─X1400
                  \
                   B
```

`A→X1` и `X1400→B` невозможны.

Но `X1→...→X1400` может выглядеть идеально плавно.

Поэтому detector не может ограничиваться тройками соседних точек.

## 4. Stage A — local transition analysis

Для соседних valid-position records:
- вычислить `dt`;
- distance;
- apparent speed;
- robust baseline activity statistics.

Результат:
- normal;
- suspicious;
- impossible.

## 5. Robust baseline

Использовать устойчивые статистики:
- median;
- percentiles;
- MAD при необходимости.

Не использовать среднее как единственный baseline.

## 6. Absolute vs relative evidence

### Absolute evidence

Пример: километры за секунду.

Это HIGH-confidence physical impossibility независимо от среднего темпа.

### Relative evidence

Сегмент может быть подозрительным относительно типичной активности, но этого недостаточно для автоматического удаления.

Relative evidence повышает score, но не заменяет physical constraints.

## 7. Stage B — island search

После impossible transition `A→X` искать в разумном временном окне:
- candidate exit `Y→B`;
- plausible bridge `A→B`.

Сильная структура:

```text
A→X = impossible
X...Y = arbitrary
Y→B = impossible
A→B over elapsed time = plausible
```

Если выполнена — `X...Y` является strong spoofing-island candidate.

## 8. Reachability

Нужно уметь оценивать `Pi→Pj`, где `j > i+1`.

Не строить полный O(n²) graph.

Стратегия v0.1:
- соседние transitions всегда;
- expanded search только вокруг impossible/suspicious entry;
- ограничение по времени;
- возможно exponentially growing windows / pruning.

## 9. Missing positions

Records без lat/lon:
- не считаются teleport;
- классифицируются отдельно как missing GNSS;
- могут формировать interval;
- reconstruction решает их отдельно.

Missing records могут скрыть обратный impossible transition. One-sided scan запускается
только от unpaired `IMPOSSIBLE` entry и ограничен числом records. Обычный tunnel/dropout
без impossible entry не создаёт corrupted interval.

Явное course-backed заполнение missing prefix/suffix остаётся отдельной reconstruction
операцией: оно не меняет эту классификацию, не создаёт detector evidence и не превращает
сгенерированные coordinates в trusted observations. Его confidence не выше `MEDIUM`.

## 10. Course independence

До конца Integrity Detection алгоритм не должен знать:
- planned course;
- OSM;
- distance to route.

Это обязательный regression invariant.

## 10A. Geometry gap diagnostics

Файл без timestamps может содержать длинную последовательность точек, равномерно
лежащих на почти идеальном chord. Соседние расстояния при этом малы, поэтому physical
transition detector обоснованно возвращает `UNKNOWN`, а не corruption.

Отдельный diagnostic pass измеряет:
- длину chord;
- число positioned observations;
- sampled path/chord ratio;
- максимальное поперечное отклонение от chord.

Совпадение строгих геометрических признаков создаёт только
`possible_interpolated_gnss_gap / LOW`. Оно не меняет общий status, не является
`CorruptedInterval` и не допускает repair. Настоящая длинная прямая в принципе может
иметь похожую форму; provenance интерполяции из одной геометрии доказать нельзя.

Поиск выполняется ограниченными окнами с фиксированным stride и не строит O(n²) graph.
Явные continuity boundaries не пересекаются. Course и внешний map matching запрещены.

## 11. Time integrity

По умолчанию:
- timestamp trustworthy;
- position suspicious.

Detector не исправляет время.

Отдельная time-integrity subsystem может появиться позже, но не входит в v0.1.

## 12. Evidence model

Пример interval evidence:

```text
+ impossible_transition_in
+ impossible_transition_out
+ plausible_bridge
+ consistent_real_track_before
+ consistent_real_track_after
```

Confidence должен вычисляться из документированных rules, а не из LLM.

Classic island с impossible entry/exit получает `HIGH`. One-sided missing-exit proof
никогда не получает выше `MEDIUM` и поэтому не применяется writer-ом при default
`--min-confidence high`.

Composite region не является новым доказательством corruption. Он запускается вокруг
существующего course-independent interval при unsafe immediate anchor и лишь агрегирует
ordered positioned/missing components в bounded diagnostic region. Component с
достаточным NORMAL context остаётся `PLAUSIBLE`, недостаточно доказанный — `UNKNOWN`;
ни course proximity, ни внешний bridge не повышают эти состояния. `TAINTED` означает
локальное abnormal transition evidence и ограничивает возможный candidate уровнем
`MEDIUM`.

## 13. False positives

Особо тестировать:
- steep downhill;
- trail switchbacks;
- wrong turns;
- out-and-back;
- loops;
- pauses;
- irregular recording cadence;
- missing records;
- race start congestion.

## 14. Первичный acceptance target

На приватном Andromeda FIT:
- без GPX;
- найти главный spoofing island;
- boundary tolerance на раннем этапе ±30 секунд;
- HIGH confidence;
- не требовать знания официальной трассы.
