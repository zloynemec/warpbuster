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
