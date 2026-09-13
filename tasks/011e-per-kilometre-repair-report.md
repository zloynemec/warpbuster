# Task 011E — Покилометровая таблица восстановленного FIT

## Цель

Дополнить общий HTML-шаблон подробной таблицей по каждому полному километру
recorded distance и финальному неполному сплиту.

## Поля строки

- номер километра или фактический диапазон последнего остатка;
- длина split и elapsed time между интерполированными distance boundaries;
- unsmoothed набор и спуск по record altitude;
- ЧСС, средневзвешенная по времени только по интервалам с двумя значениями;
- cadence как среднее записанных samples внутри distance split; для running значение
  показывается в steps/min как `2 × (cadence + fractional_cadence)`;
- доля split с координатами в процентах.

`distance с координатами` — пересечение split с неубывающими record-distance edges,
у которых обе output records имеют координаты и принадлежат одному `continuity_id`.
Missing bridge, показанный пунктиром только для понимания связи, не считается координатной
геометрией. После успешной реконструкции записанные координаты автоматически входят в
расчёт. Для analyze применяется та же формула к исходной activity.

## Инварианты и ограничения

- Не менять FIT, detector, reconstruction, distance, timestamps или sensors.
- Не вычислять покрытие по хорде, GPX/course или экранной геометрии карты.
- Не интерполировать ЧСС через missing sensor endpoint; missing cadence samples исключать,
  а нулевые сохранять как записанные значения.
- Сохранять прежние определения pace/elevation split и отдельно маркировать остаток.
- При неполной/non-monotonic distance или timestamp stream таблица остаётся недоступной,
  как и прежние split charts.

## Acceptance criteria

- [x] JSON каждого split содержит time/pace/ascent/descent/HR/cadence/coordinate distance.
- [x] HTML показывает одну читаемую таблицу для исходного или записанного repaired FIT.
- [x] Missing coordinates и continuity boundaries уменьшают coordinate coverage без chord.
- [x] Полный и неполный split, sensor gaps, time-weighted HR и sample-average cadence
  покрыты тестами.
- [x] Полный suite, Ruff, mypy и `git diff --check` проходят.

## Файлы

`report/html.py`, общий `report/assets/report.html`, report tests, README и ROADMAP.

## Результат

Завершена 2026-09-13. Общий HTML-шаблон показывает полный и финальный неполный
recorded-distance split с elapsed time, ascent/descent, time-weighted HR,
sample-average cadence и строгим coordinate coverage. Неизвестные missing bridges,
continuity boundaries и sensor gaps не интерполируются; running cadence учитывает FIT
fractional cadence и переводится из strides в steps/min. Проверки: 465 passed,
6 skipped; Ruff, mypy и `git diff --check` пройдены.
