# Машиночитаемое описание текущего алгоритма

[algorithm/flow.json](algorithm/flow.json) описывает действующий pipeline, точки входа,
приоритеты решений, профили, отдельные confidence policies, FIT-only gate, GPX/OSM/DEM,
запись FIT, отчёты, GPS CSV и варианты отказов. Ключи и идентификаторы — английские,
объяснения — русские. Это описание реализованного поведения, а не план развития.

[algorithm/implementation.json](algorithm/implementation.json) дополняет его точным
снимком реализации: все Python-модули Core, OSM Manager, OSM Routing и Web backend,
локальные JS-модули, публичные конфигурационные примеры, manifests зависимостей,
Dockerfile и шаблон результата. Каталог большой намеренно: краткий пересказ не
сохранил бы все ветви, проверки, defaults и причины отказов.

## Как читать JSON

- `flow.json` использует `schema_version=2`: `entrypoints[].start` задаёт начальную
  стадию, а единый `transitions[]` содержит `from`, `to`, `kind` и условие `when`.
  `kind=next` обозначает продолжение, `kind=substage` — вспомогательный разбор
  внутри родительской стадии с возвратом в неё. Для перехода с версии 1 замените
  чтение отдельных `next` на обход `transitions`. Самостоятельные paths находятся
  в `secondary_paths`; они не обязаны входить в общий pipeline.
- Граф — схема процесса, не исполняемая модель условий и не точный call graph.
  `when/then`, ранние возвраты и исключения объясняются в правилах стадий;
  фактические return/raise остаются в `program`. `scenario_matrix` показывает
  семейства исходов; `entrypoints[].exit_codes` — порядок исходов CLI.
- `implementation` в элементах flow содержит ссылки вида `путь.py::Class.method`.
  Они разрешаются в `implementation.json → modules[].symbols[].id`.
- `modules[].program` — структурированные statements Python 3.14 AST. `body`,
  `orelse`, `handlers`, `finalbody` сохраняют порядок и вложенность. Expression
  представлен объектом `{"python": "..."}`; это реальное выражение реализации,
  а не новый язык policy. `decorator_list`, параметры и значения по умолчанию,
  наследование, assignments, calls, returns и raises также сохранены.
- `decision_points` индексирует условия, циклы, inline ternaries, Boolean
  short-circuit, comparisons, comprehensions и explicit exception paths.
  `line` указывает в исходный файл; `node` — ordinal исходного `ast.walk`.
  Истинная/ложная ветвь и обработка исключений читаются в `program`.
- `symbols` содержит docstrings, аргументы, annotations, объявленные поля/defaults,
  assigned members (включая enum values) и диапазоны строк. `required=true` означает
  отсутствие default в теле этого класса; наследование/factories смотрите в
  `program`. Comments на строке поля сохранены в `source_line`; описания единиц
  также есть в docstrings конфигурационных классов. Docstrings местами исторические:
  при расхождении приоритет имеет код `program`, затем пояснения flow.
- `resources[].source` хранит дословные JS/config/template sources. Для JavaScript
  отдельный AST/branch index не заявляется. Сторонние vendor assets не копируются.
- Суммы, сортировки, арифметика, включённые границы сравнений и fallback order
  доступны в точных expressions. Все конфигурационные варианты включены как
  declarations/factories/profile/env/CLI rules, а не только numeric defaults.

`source_digest_sha256` и `sha256` каждого файла определяют описанный snapshot.
Для проверки не нужен доступ к Git, сети, пользовательским трекам или `.env`.
Приватная production-конфигурация, данные активности, cookies и диагностика
пользователей в артефакт не включаются.

`RESOURCE_PATHS` в генераторе — явный список включённых JS/config/template файлов.
Посторонний `debug.js` не попадёт в документ. При добавлении нового локального
JS-модуля обновите этот список; отсутствие перечисленного ресурса — ошибка с его
именем. Python-модули по-прежнему собираются из четырёх объявленных source roots.

## Проверка и обновление

Из корня проекта:

```sh
.venv/bin/python scripts/export_algorithm.py --check
.venv/bin/python -m pytest -q tests/test_algorithm_documentation.py
```

При изменении алгоритма сначала обновите соответствующие объяснения/ссылки в
`flow.json`, затем пересоздайте точный каталог и проверьте его:

```sh
.venv/bin/python scripts/export_algorithm.py
.venv/bin/python scripts/export_algorithm.py --check
```

Генератор использует только стандартную библиотеку и разбирает исходники через AST;
не импортирует и не запускает код приложения. Снимок создан CPython 3.14.7;
`generator.python_implementation` и `generator.python_version` фиксируют среду
каждой генерации. Выход воспроизводим при одинаковых исходниках и фиксированной
версии/реализации Python; текущие время, Git commit, optional native dependencies
и сеть на него не влияют. Между версиями Python AST и `ast.unparse` могут отличаться,
поэтому после смены интерпретатора `--check` может показать stale даже без изменения
Core. Сравните метаданные среды и diff перед перегенерацией. Это не ограничивает
поддерживаемые версии Python самого приложения.

Экспорт и `--check` проверяют структуру flow, типы ссылок/правил, уникальность IDs и
ссылок, существование символов/ресурсов, корректность переходов и достижимость
каждой стадии из entrypoint. Тесты дополнительно проверяют ошибочные структуры,
неизвестные ссылки, недостижимые подстадии, явный состав ресурсов, метаданные среды
и сохранение nested/exception/inline branches. JSON читается и записывается в UTF-8.
Совпадение snapshot с кодом не доказывает истинность каждого ручного пояснения:
при semantic изменении его необходимо отдельно пересмотреть.

## Граница полноты

Включены все синтаксические ветви перечисленных файлов, в том числе compatibility,
диагностические и admin paths; само присутствие функции в каталоге не означает её
вызова из `run_repair`. Исторические Markdown-документы проекта не используются
как источник действующего алгоритма.

Входных треков, состояний внешних сервисов и последовательностей исключений
бесконечно много. Каталог не перечисляет каждое исполнение и не доказывает
достижимость ветвей. Внутренние алгоритмы сторонних библиотек/Valhalla/OS не
переопределяются: сохранены границы вызова, используемые версии/ограничения,
проверки ответов и fallbacks собственного кода. Implicit Python/dependency
exceptions определяются их semantics; explicit `try`/`except`/`finally` сохранены.

Нет обещания, что каждый возможный маршрут найден, что distance/speed независимы
от GNSS или что восстановленная geometry совпадает с фактическим движением.

## Приватная диагностика OSM

Неожиданные исключения внешнего OSM boundary, внутренних coverage/routing catches
и изолированного child сохраняются в `OSMResult.private_audit.failure.diagnostic`.
Общий форматтер `warpbuster.diagnostics` ограничивает `exception_type` до 256,
`reason` до 4096 и `traceback` до 32768 символов. Сохраняются stack locations и
chained exceptions, без frame locals; усечение может удалить раннюю часть цепочки.
Surrogateescaped native paths экранируются, чтобы audit оставался UTF-8 writable.
Если форматирование само падает, остаётся ограниченное имя типа исключения.

Существующие public stage/error codes и безопасный base-plan fallback сохранены.
`failure.stage` обозначает обработавшую ошибку границу; точное место ищите в
traceback. В частности, child сохраняет прежний public `setup/resource_limit`,
даже если исходное исключение возникло позднее внутри optional stage.
Web пишет этот объект в существующий `private-osm-audit.json` с mode 0600;
файл и диагностические строки не выдаются ни владельцу, ни гостю через HTTP.
Локальный CLI может включать audit в свой собственный JSON-отчёт.

SIGKILL, segfault и отсутствие IPC-ответа могут не оставить Python exception,
поэтому существующие timeout/resource/IPC safe outcomes не обещают traceback.
Новые retries, внешние сервисы диагностики и дополнительные данные активности
не добавляются.

## История

- 2026-11-18: добавлены описание текущего алгоритма, полный каталог ветвей и
  проверка актуальности, чтобы отделить реализованное поведение от исторических
  описаний и не терять редкие варианты при кратком пересказе.
- 2026-11-19: flow переведён на schema 2 с едиными переходами; добавлены бюджет
  базового плана, duplicate gap candidates и точные исходы CLI, включая NOT_NEEDED
  до coverage refusal и ошибку HTML после записи FIT. Усилена проверка структуры,
  зафиксирована среда генерации и задан явный список JS-ресурсов.
- 2026-11-19: добавлена bounded private диагностика неожиданных OSM exceptions
  с сохранением fallback и прежних public codes. Web переиспользует Core formatter;
  добавлены проверки цепочек, UTF-8, permissions, HTTP privacy и сохранности FIT.
