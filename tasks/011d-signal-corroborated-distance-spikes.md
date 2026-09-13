# Task 011D — Signal-corroborated distance spikes в неизвестной геометрии

## Выявленная проблема

Между двумя устойчивыми участками FIT может находиться длинный GNSS dropout с
несколькими короткими островками координат. Переход к такому островку иногда выглядит
физически допустимым из-за большого elapsed time всего dropout, поэтому обычный
detector соседних positioned observations не видит невозможную границу. Одновременно
одна record за секунды добавляет километры в cumulative `distance`, хотя полный
локальный `speed` stream описывает лишь десятки или сотни метров. Результат сохраняет
лишние километры даже после удаления других очевидных teleport-ов.

Это общий дефект доказательства и коррекции, а не задача восстановления формы одного
конкретного маршрута. GPX/OSM не должны влиять на классификацию.

## Решение и границы

Добавить ограниченный course-independent proof для одного скачка `record.distance`:

1. прирост за соседние timestamps превышает running physical ceiling и именованный
   minimum increment;
2. от последней positioned опоры до record имеется полный, конечный и физически
   допустимый `speed` stream;
3. `distance` до самого последнего скачка согласуется с интегралом speed — это исключает
   нормальное отложенное обновление odometer;
4. recorded position displacement одновременно существенно и кратно превышает путь
   по speed;
5. все пороги, предел длины поиска, размера positioned island и числа evidence находятся
   в `IntegrityConfig`.

Proof имеет максимум `MEDIUM`: происхождение FIT distance/speed не угадывается, и эти
поля не объявляются независимыми датчиками. Короткий максимальный positioned island,
ограниченный missing records, можно инвалидировать только при явном
`--min-invalidation-confidence medium`. Сам доказанный distance step корректируется
только при `--min-confidence medium`.

Коррекция заменяет один невозможный increment трапецеидальным интегралом сохранённого
speed на той же паре timestamps и сдвигает только следующие cumulative distance и
зависимые lap/session totals/average speed. Writer повторно вычисляет proof по исходной
activity перед записью.

Форму неизвестного участка не угадывать: не строить прямую как новые FIT coordinates,
не ослаблять GPX matching и не запускать routing. После invalidation соседние missing и
короткие островки образуют один gap. В HTML он остаётся пунктирным presentation bridge;
это не записанная реконструкция.

## Не входит в задачу

- общий поиск всех видов рассогласования coordinate/odometer из Task 005C;
- определение производителя или источника distance/speed;
- восстановление неизвестной геометрии по прямой, GPX или OSM;
- исправление incomplete, decreasing, reset или противоречивых streams;
- изменение timestamps, events, speed, sensor, altitude, developer/unknown fields;
- ослабление default `HIGH` application policy.

## Acceptance criteria

- [x] Длинный missing span с физически допустимым outer transition не скрывает
  доказанный impossible distance step.
- [x] Короткие signal-corroborated positioned islands получают только `MEDIUM` и
  инвалидируются только по явному medium threshold; stable recovery сохраняется.
- [x] Доказанные steps корректируются локально; cumulative distance и summaries
  согласованы, timestamps/speed/sensors и исходный FIT неизменны.
- [x] Delayed odometer catch-up, неполный speed stream и отключённый evidence budget
  не приводят к коррекции.
- [x] Proof повторно проверяется mask/writer; подменённый plan не публикуется.
- [x] Console/JSON/общий HTML показывают thresholds, original/replacement increment,
  удалённую дистанцию и то, что geometry остаётся unresolved.
- [x] Синтетические regressions, полный Core suite, Ruff, mypy и `git diff --check`
  проходят; приватные активности используются только как smoke-test.

## Затронутые файлы

`config.py`; модели integrity/reconstruction; новый `integrity/odometer.py`;
detector, coordinate mask, selection, FIT writer; console/JSON/HTML reporting;
синтетические тесты, README, architecture/detection decisions и ROADMAP.

## Результат — 2026-09-13

Реализован отдельный `integrity/odometer.py`: он собирает capped evidence, формирует
только короткие `signal_corroborated_island / MEDIUM` intervals и повторно проверяет
threshold snapshot перед mask/write. Selection не дублирует step, уже пересчитанный
выбранной coordinate geometry. Writer атомарно применяет cumulative correction и
поддержанные summaries; FIT semantic diff и повторное кодирование подтверждают точный
scope. Общий HTML дополнен таблицей original/replacement/removed distance steps.

Добавлено 6 сквозных синтетических regressions и расширены config/CLI/private safety
tests. Проверены длинные dropout-ы с normal outer transitions, stable recovery, default
HIGH no-op, explicit MEDIUM, delayed catch-up, missing speed, zero evidence budget,
подменённый proof, отсутствие invented coordinates и summary consistency.

```bash
.venv/bin/pytest -q
.venv/bin/ruff check src tests scripts
.venv/bin/ruff format --check src tests
.venv/bin/mypy src
git diff --check
```

Результат: **462 passed, 6 skipped**; Ruff, mypy и diff checks пройдены. Приватный
smoke-test нашёл пять доказанных steps, объединил 11 коротких positioned observations
с соседним missing gap и изменил embedded distance с 32.36896 до 20.93064 km. Исходный
FIT, timestamps, speed/sensors, developer/unknown fields сохранены; CRC и semantic diff
валидны. Geometry намеренно осталась unresolved и в FIT не дорисована.

Ограничения: proof работает только для sport profile с явным physical ceiling и полного
локального speed stream; confidence остаётся MEDIUM. Неоднозначная геометрия, общий
odometer diagnostic Task 005C и routing не реализованы. Все acceptance criteria Task
011D выполнены.
