# Task 006D — Course-backed Missing-position Completion

Статус: выполнена.

> Историческая постановка. [Task 011](011-local-gpx-gap-reconstruction.md) заменяет
> глобальный longest-run gate и endpoint-only ограничения единым локальным planner
> для prefix/internal/suffix gaps. `--fill-missing-from-course` по-прежнему достаточно
> для принятия course endpoints как допущения; отдельного подтверждения не требуется.

## Проблема

Активность может содержать правдоподобные timestamp, distance, speed и sensor streams,
но не иметь GNSS coordinates в начале или конце записи. После появления устойчивого GPS
оставшаяся geometry может хорошо и однозначно совпадать с известным GPX course.

Обычный Integrity Detector правильно не объявляет отсутствие координат corruption:
неизвестное положение не доказывает физически невозможное движение. Поэтому основной
repair planner, работающий только с corrupted intervals, в таком случае возвращает
`NOT_NEEDED`, хотя reference course может позволить явно и консервативно достроить
отсутствующую geometry.

## Цель

Добавить отдельный reconstruction provider для явного course-backed заполнения
отсутствующих координат на границах активности, не ослабляя detector и не изменяя
существующие правдоподобные GPS observations.

Решение не привязано к устройству, спортсмену, конкретному маршруту, длине тренировки
или номерам records. Приватная тренировка используется только как regression fixture.

## Архитектурная граница

Pipeline остаётся разделённым:

1. FIT normalisation и Integrity Detection выполняются без course;
2. основной corruption repair planner строит candidates для доказанных failures;
3. только при явном `--fill-missing-from-course` независимый missing-position planner
   рассматривает отсутствующие endpoint coordinates;
4. результаты providers объединяются в один `RepairPlan`;
5. одна selection policy и один атомарный writer pass применяют выбранные candidates.

Missing planner не запускается «после исправленного файла» и не использует результаты
первого writer pass как новые evidence или anchors. При пересечении scopes основной
corruption candidate имеет приоритет, а missing candidate отклоняется с явной причиной.

## Scope v0.1

Поддерживаются только максимальные missing runs:

- от первого record до первой существующей позиции (`PREFIX`);
- после последней существующей позиции до последнего record (`SUFFIX`).

Внутренние clean gaps, активность без единого positioned run, несколько раздельных
course segments и reconstruction без reference course в эту задачу не входят.

## Proof и safety policy

Candidate разрешён только если одновременно выполнены общие условия:

- найден достаточно длинный непрерывный observed positioned run;
- все соседние transitions внутри него классифицированы `NORMAL` независимым detector;
- начало и конец observed run проецируются на один course segment;
- направление и branch course однозначны с учётом anchor errors и записанной distance
  observed run;
- относительное расхождение recorded distance и course span не превышает configurable
  threshold;
- traversal missing path и каждый новый boundary transition физически правдоподобны;
- число records ограничено configurable bound;
- исходные FIT message definitions уже содержат `position_lat` и `position_long` с
  invalid value, поэтому writer может выполнить fixed-width patch без изменения schema.

Course proximity сама по себе недостаточна. Altitude не используется как proof и не
изменяется. Existing positioned records, timestamps, speed, altitude, heart rate,
cadence, power, developer и unknown fields остаются неизменными.

Любой missing-position candidate имеет максимум `MEDIUM`, `repair_eligible=false` при
default `HIGH` и применяется только при одновременном явном выборе:

```bash
warpbuster repair activity.fit --course route.gpx \
  --fill-missing-from-course --min-confidence medium
```

## Geometry allocation и distance semantics

Для prefix/suffix planner строит путь от соответствующего endpoint course до
проекции существующего GPS anchor и добавляет короткий connector к самой observation.
Records распределяются по этому пути первым пригодным сигналом:

1. recorded cumulative distance;
2. integrated recorded speed;
3. timestamps;
4. record order.

Recorded FIT distance считается независимым правдоподобным odometer stream. В этом
режиме он управляет allocation, но не пересчитывается из сгенерированной geometry.
Session/lap distance и speed summaries также сохраняются. Report явно показывает
`distance=preserved`, allocation source, observed/course consistency, course span,
connector, число созданных coordinates и причины отказа.

Write-mode HTML после повторного чтения validated output также показывает итоговый
средний темп, темп по recorded-distance километрам, FIT total ascent/descent и отдельные
суммы подъёма/спуска внутри каждого километра. Поэтому холм с равными подъёмом и спуском
не исчезает как нулевой net change. Последний неполный split остаётся видимым и получает
нормализованный темп.

## Configuration

Все новые bounds находятся в `CourseReconstructionConfig`:

- `missing_alignment_min_position_records` — minimum records устойчивого observed run;
- `missing_alignment_max_distance_ratio_error` — maximum relative mismatch его
  recorded distance и course span;
- `missing_completion_max_course_speed_mps` — maximum average missing-path speed;
- `missing_completion_max_connector_speed_mps` — maximum new local transition speed;
- `missing_completion_max_run_records` — hard work/output bound на один endpoint run.

## Acceptance criteria

- режим выключен по умолчанию, а прежний `repair` не меняет поведение;
- detector не принимает course и активность с missing coordinates остаётся
  `UNKNOWN`, если нет отдельного corruption evidence;
- explicit mode создаёт независимые `PREFIX`/`SUFFIX` candidates максимум `MEDIUM`;
- существующие positioned records не входят в updates;
- ambiguous, inconsistent, unstable, excessive или structurally unpatchable cases
  получают machine-readable unresolved reason;
- основной repair candidate имеет приоритет при overlap;
- writer применяет все выбранные providers одним atomic pass;
- recorded distance, timestamps, sensors, definitions и unknown/developer data
  сохраняются;
- console/JSON/HTML перечисляют каждый applied/skipped/unresolved missing target;
- synthetic tests покрывают success, default skip, field preservation и refusal;
- private regression подтверждает полное заполнение endpoint gaps, valid CRC,
  отсутствие unexpected diff и физически правдоподобные итоговые transitions;
- полный test/lint/type-check suite зелёный.

## Реализованный private regression

Приватный fixture содержит длинный missing prefix, устойчивый observed GPS run и
короткий missing suffix. Planner однозначно сопоставляет observed run с course, создаёт
два `MEDIUM` candidate и заполняет только исходно отсутствующие position fields.

Private fixture не коммитится и не задаёт thresholds. Его конкретные distances,
records и coordinates не используются в production branches.

## Сознательно не реализовано

- заполнение внутренних clean GNSS gaps без независимого failure evidence;
- замена существующих правдоподобных coordinates из-за близости course;
- использование altitude как доказательства маршрута;
- изменение timestamps или recorded distance под новую geometry;
- добавление отсутствующих FIT fields/definitions;
- automatic `HIGH` application;
- OSM routing, DEM и reconstruction без эталонного course.
