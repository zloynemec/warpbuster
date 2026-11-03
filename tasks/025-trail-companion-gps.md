# Task 025 — Trail Companion GPS Analysis

Статус: реализован 2026-11-03; CLI, Python API, synthetic и private smoke tests.

## Scope

CLI и Python API для `locations.csv`, извлечённого из ZIP-сессии Trail Companion.
Только GPS: без web, событий, батареи, отметок, course, OSM/DEM, реконструкции или
исправления исходного файла. Использовать существующий Integrity Detector.

## Contract and decisions

Входные поля и политика статусов зафиксированы в
[CLI_SPEC §2.1](../docs/CLI_SPEC.md#21-warpbuster-analyze-gps).
Порядок — уникальный числовой `seq`, не timestamp. Связь seq сохраняется в JSON
и source ref нормализованного ActivityRecord. Отрицательные sensor measurements
недоступны. Флаги рекордера не являются решением WB.

По согласованию: default running/trail, с явным допущением в JSON и выбором generic.
Предел 25 м/с взят из существующего running detector; независимого детектора нет.
HIGH interval → rejected; неоднозначные переходы/интервалы → uncertain. Trusted
требует приемлемую заявленную точность и положительное локальное NORMAL evidence.
Пороги принятия по умолчанию: 25 м / 60 с; конфигурируются и тестируются отдельно
от физического доказательства corruption.

## Acceptance criteria

- Каждая входная seq ровно один раз, детерминированный статус, причины и evidence.
- Валидировать обязательные поля, UTC timestamps, координаты, конечные числа и seq.
- Повторно использовать `analyze_integrity`; не менять его алгоритмы/thresholds.
- Синтетический телепорт с возвратом исключается без исключения нормальных соседей.
- Проверить время/разрывы, недоступную скорость, плохую точность, остановки,
  развороты, единичные и неоднозначные наблюдения, generic profile.
- GPX содержит только trusted исходные точки, сохраняет координаты, время и seq
  order; omitted points и долгие разрывы разделяют сегменты. Явный пустой результат.
- CLI и API совпадают. Исходник неизменен; no-overwrite default, explicit overwrite
  не позволяет записать поверх исходника, включая файловые aliases.
- Предыдущие FIT/GPX tests, lint и type checks остаются зелёными.
- Реальные данные и полученные треки остаются в ignored private storage.

## Verification

```bash
.venv/bin/python -m pytest tests/test_gps.py -q
.venv/bin/python -m pytest -q
.venv/bin/ruff check src tests
.venv/bin/mypy src/warpbuster
```

Проверено: 35 новых synthetic tests и 1 private smoke test; полный Core suite —
815 passed, 6 skipped. Ruff lint/format, mypy и `git diff --check` проходят.
Acceptance criteria выполнены в указанном scope; ground truth для реальной записи
не предоставлен.

Приватная запись: 754 точки; 676 trusted, 36 rejected, 42 uncertain;
максимальный временной разрыв 149,046 с. Это smoke test, не ground truth разметка.
Public tests используют только синтетические данные.
Результирующий приватный GPX: 676 исходных точек, 7 сегментов; исходный CSV
побайтово совпадает с данными ZIP. Производительность на синтетических 20 000
точек: анализ 0,435 с, вместе с JSON/GPX 0,703 с (один локальный замер).

## Limits

Trusted означает принятие по явной политике WB. Правдоподобные ложные координаты
могут остаться нераспознанными; заявленная accuracy не является независимой истиной.
Running default непригоден для всех видов транспорта; для них выбирается generic.
Каждый output публикуется атомарно, но общей транзакции двух файлов нет.
