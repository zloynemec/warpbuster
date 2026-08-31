# Codex Workflow — WarpBuster Core

## Основное правило

Codex получает **один task за один цикл разработки**. Не отдавать ему весь `docs/MILESTONES.md` как команду «реализовать всё».

## Что положить в репозиторий до начала

Скопировать в root проекта:

- `AGENTS.md`
- `README.md`
- `docs/`
- `tasks/`

Реальные пользовательские FIT/GPX хранить локально в `tests/private/`, не коммитить по умолчанию.

## Шаблон задания Codex

Для каждого этапа использовать примерно такой запрос:

```text
Прочитай AGENTS.md.

Текущий этап разработки: tasks/00X-....md.
Также прочитай только релевантные документы из docs/, на которые ссылается задача.

Выполни ТОЛЬКО этот этап. Не реализуй функциональность следующих milestones.

Перед изменениями:
1. дай короткий план;
2. перечисли файлы, которые собираешься создать/изменить;
3. назови спорные технические решения.

После реализации:
1. запусти тесты/линтер/type-check, предусмотренные проектом;
2. сравни результат с Acceptance Criteria текущего task;
3. перечисли, что намеренно оставлено на следующие этапы;
4. не переходи к следующему task.
```

## Порядок этапов

1. `001-project-bootstrap.md`
2. `002-fit-reader-inspect.md`
3. `003-local-transition-detector.md`
4. `004-spoofing-islands.md`
5. `005-safety-regressions.md`
6. `006-course-repair-plan.md`
7. `007-fit-writer-validation.md`
8. `008-html-report-release.md`

## Контрольные точки

### После 001
Есть только каркас проекта. Никакого FIT.

### После 002
Можно выполнить:

```bash
warpbuster inspect activity.fit
```

и увидеть корректно разобранную активность. Никакой диагностики GPS ещё нет.

### После 003
Можно выполнить:

```bash
warpbuster analyze activity.fit
```

и увидеть локальные impossible transitions. Длительный spoofing island ещё может не быть объединён.

### После 004
Ключевой proof-of-concept:

```bash
warpbuster analyze andromeda.fit
```

**без GPX** должен определить длинный spoofing island.

Если это не работает надёжно — не начинать repair.

### После 005
Detector должен пройти false-positive regressions. Это safety gate перед repair.

### После 006
Есть `RepairPlan` и `--dry-run`, но исходный FIT ещё физически не меняется.

### После 007
Впервые появляется реальный исправленный FIT. Именно здесь вручную тестируем его в FIT viewer / Garmin Connect / Strava.

### После 008
Получаем законченную локальную v0.1 с HTML-диагностикой.

## Когда останавливать Codex и обсуждать решение

Остановиться до реализации, если агент предлагает:

- использовать distance-to-course в Integrity Detector;
- snap всего трека на GPX;
- менять timestamps из-за невозможной GPS-скорости;
- конвертировать FIT в GPX как canonical pipeline;
- сделать полный O(n²) reachability;
- начать OSM/Strava/Garmin API раньше v0.1;
- автоматически исправлять LOW/MEDIUM-confidence intervals.

Это противоречит базовым архитектурным решениям проекта.
