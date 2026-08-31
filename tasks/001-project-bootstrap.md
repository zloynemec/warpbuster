# Task 001 — Project Bootstrap

## Цель

Создать минимальный, чистый Python-проект WarpBuster Core без реализации FIT parsing и detector logic.

## Сделать

- Python 3.14+ package с `src/` layout.
- `pyproject.toml`.
- CLI entry point `warpbuster`.
- Команды-заглушки допустимы только если нужны для проверки CLI.
- pytest.
- lint/format/type-check configuration по разумному современному стеку.
- `.gitignore`, включая `tests/private/`.
- базовые domain/config modules без premature complexity.
- `warpbuster --version`.
- README development commands.

## Не делать

- FIT parsing.
- GPS algorithms.
- GPX.
- repair.
- HTML.
- web/API/database.

## Acceptance Criteria

- fresh install проходит по README;
- `warpbuster --version` работает;
- `pytest` проходит;
- lint/type-check commands проходят;
- package импортируется;
- нет бизнес-логики будущих milestones.

## Перед завершением

Показать:
- tree проекта;
- команды install/test/lint;
- список сознательно отложенных вещей.
