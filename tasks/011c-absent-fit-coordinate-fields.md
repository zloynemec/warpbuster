# Task 011C — Запись отсутствующих coordinate fields в FIT

## Проблема и границы

FIT record может содержать время и датчики, но не иметь position_lat/position_long
в definition. Пригодный кандидат реконструкции нельзя записать обычным патчем
существующих полей. Это не доказательство повреждения GPS и не особый алгоритм
для конкретного производителя.

Добавлять только отсутствующие native coordinate fields для записей уже выбранного
кандидата. Исходные пропуски остаются opt-in через --fill-missing-from-course.
Не добавлять records, не менять timestamps, events, sensors, unknown/developer fields,
достоверные координаты и правила проверки кандидатов. Не сглаживать distance и
не интерпретировать происхождение данных POD. Routing вне объёма.

## Реализация

Вставить временную definition с native sint32 coordinate fields для конкретной
data message, добавить значения перед developer payload, немедленно восстановить
исходную definition того же local ID. Не перекодировать существующие поля.
Пересчитать размер контейнера, header/file CRC. Прежний путь без расширений сохраняется.
Явно отражать schema change и число добавленных полей/definitions в FIT diff и HTML.
Публиковать файл атомарно после CRC, semantic diff, scope и geometry verification.

## Acceptance criteria

- [x] Prefix/internal/suffix с пригодными кандидатами записываются при отсутствии полей.
- [x] Поддержаны отсутствие одного/обоих полей, endian, developer payload; соседние
  записи с тем же local ID и исходные definitions сохранены.
- [x] Другие отсутствующие поля и переполнение field count не допускаются.
- [x] Сохранены события, timestamps, sensors, unknown/developer fields и исходный FIT.
- [x] Непригодные кандидаты остаются unresolved; Garmin/COROS проходят прежние тесты.
- [x] Добавлены синтетические тесты, пройдены полный suite, lint и type checking.

Файлы: fit/writer.py, fit/diff.py, models/fit.py, reconstruction/gaps.py,
report/fit.py, общий HTML-шаблон, тесты writer/reconstruction, ROADMAP и README.

## Результат — 2026-09-04

Реализованы локальное расширение definition, вставка native координат перед developer
payload, восстановление исходной definition, обновление container size/header CRC/file
CRC и schema audit в console/JSON/HTML. Публикация сверяет ожидаемые добавления,
точные выходные байты, semantic diff и принятую геометрию; записи не добавляются.

19 новых тестов: 18 синтетических и одна доступная приватная регрессия. Прежний
тест отказа без coordinate fields заменён сквозной проверкой трёх видов gaps.
Проверены 12/14-byte headers, endian, одно/два поля, shared local ID, compressed
timestamps, developer/unknown bytes, opt-in, selection, atomic refusal и HTML audit.
Расширенные синтетические файлы дополнительно читаются независимым Garmin FIT SDK.

```bash
.venv/bin/pytest -q
.venv/bin/ruff check src tests scripts
.venv/bin/ruff format --check src tests scripts
.venv/bin/mypy src
git diff --check
.venv/bin/python -u scripts/repair_pairs.py tests/repair_pairs.csv --report-dir /private/tmp/warpbuster-reports.rJevk4IW
```

Результат: **427 passed, 6 skipped**, Ruff/mypy пройдены; 8/8 batch runs успешны.
Все восемь выходных FIT побайтно совпадают с предыдущими: новые поля там не
потребовались для принятых кандидатов. Исходники неизменны. Дополнительная проверка
в памяти покрыла отсутствующие coordinate schemas реального COROS-экспорта, без
публикации искусственных тестовых координат. HTML-отчёты обновлены.

Ограничения: пригодность пути проверяется по-прежнему; скачки distance после пауз
могут отклонять реконструкцию и требуют отдельного решения. Новые координаты не
добавляются принудительно при отказе кандидата. По две служебные definitions на
расширенную record увеличивают размер файла; объединение последовательных
расширений не реализовано. Все acceptance criteria текущего task выполнены.
