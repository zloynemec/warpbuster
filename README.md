<p align="center">
  <img src="docs/assets/warpbuster-logo.png" alt="WarpBuster logo" width="320">
</p>

# WarpBuster

WarpBuster находит физически невозможные GPS/GNSS-скачки в записях тренировок
и восстанавливает повреждённые участки по GPX-маршруту. Работает с исходным FIT,
сохраняя время, спортивную телеметрию и неизвестные поля, без конвертации через GPX.

Доступны локальный CLI, HTML-отчёты с интерактивной картой и отдельный веб-интерфейс.

## Что умеет

- Читать и анализировать FIT и GPX: координаты, пропуски, невозможные переходы
  и длительные участки ложного GPS.
- Очищать доказанно ошибочные координаты и восстанавливать подходящие участки FIT
  по эталонному GPX.
- По явному запросу заполнять отсутствующие координаты, в том числе в начале
  и конце тренировки.
- Применять частичное восстановление: исправлять доступные участки и объяснять,
  почему остальные пропущены.
- Сравнивать исходный и исправленный FIT, показывать дистанцию, темп, набор/спуск
  высоты и покилометровую статистику.
- Готовить данные OpenStreetMap через OSM Manager, строить граф через OSM Routing
  и применять проверенные OSM-кандидаты после GPX.

## Принцип восстановления

> Не менять правдоподобное движение. Исправлять только доказанно невозможные GNSS-данные.

Отклонение от маршрута, петля или разворот сами по себе не считаются ошибкой.
Обнаружение повреждений работает независимо от GPX; эталон используется только
для реконструкции. Заполнение исходных пропусков координат включается отдельно.

Исходный файл не перезаписывается. В выходном FIT сохраняются timestamps, датчики
и высота; меняются разрешённые координаты и поддержанные зависимые показатели.
Дистанция корректируется локально при доказанной ошибке, а не заменяется длиной GPX.
Все изменения доступны в FIT diff.

## Установка

Требуется Python 3.14 или новее. Из исходников:

```bash
git clone https://github.com/zloynemec/warpbuster.git
cd warpbuster
python3.14 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install .
warpbuster --version
```

Команды ниже выполняются в активированном окружении. Вместо активации можно
вызывать CLI напрямую: `.venv/bin/warpbuster`.

## Быстрый старт

### Полная обработка двух файлов

Установите дополнительные пакеты для OSM из этого репозитория:

```bash
python -m pip install '.[osm]' ./packages/osm-manager ./packages/osm-routing
warpbuster process activity.fit race.gpx --html
```

Команда сама читает FIT/GPX, находит повреждения, строит GPX-план, запрашивает
необходимое OSM-покрытие через Manager, готовит граф через Routing, выбирает
восстановление и записывает `activity.fixed.fit`. `--html` добавляет отчёт
`activity.repair.html`; `--json` выводит FIT diff, план, политику и OSM audit.
Исходные файлы сохраняются. Если изменений нет, новый FIT не создаётся.

Полный пример с явными порогами `MEDIUM` и сохранением FIT, HTML и JSON
(из корня проекта; замените пути входных файлов):

```bash
.venv/bin/python -m warpbuster process \
  "/путь/activity.fit" \
  "/путь/course.gpx" \
  --fill-missing-from-course \
  --min-invalidation-confidence medium \
  --min-confidence medium \
  --osm-mode auto \
  --work-dir .warpbuster \
  --output activity.fixed.fit \
  --html activity.repair.html \
  --json > activity.repair.json
```

`--min-invalidation-confidence` задаёт порог удаления повреждённых координат,
а `--min-confidence` — порог применения восстановления. Для `process` оба порога
уже равны `MEDIUM`, заполнение пропусков и режим OSM `auto` включены по умолчанию;
в примере эти настройки указаны явно. Для замены существующих FIT/HTML добавьте
`--overwrite`. Перенаправление `>` перезаписывает JSON независимо от этого флага.

Служебные snapshots и графы сохраняются в `.warpbuster/osm/` и используются повторно.
Другую директорию задаёт `--work-dir DIR`. Режимы:

```bash
warpbuster process activity.fit race.gpx --osm-mode offline --work-dir .warpbuster
warpbuster process activity.fit race.gpx --osm-mode disabled
warpbuster process activity.fit race.gpx --dry-run --json
```

`auto` (по умолчанию) разрешает загрузку недостающего OSM-покрытия; `offline`
использует кеш Manager без сети; `disabled` оставляет GPX-восстановление.
`--dry-run` готовит тот же план и может заполнять служебный OSM-кеш, но не пишет FIT.
При недоступности OSM сохраняется пригодный GPX-план; причина отказа видна в отчёте.
Для воспроизведения с конкретным графом поддержаны `--osm-graph-id` и `--osm-cache-dir`.
Явный `--osm-graph-id` имеет приоритет над режимом подготовки: используется указанный
готовый граф, без загрузки покрытия.

`process` и веб вызывают **один API** `warpbuster.pipeline.run_repair` с единой
`DEFAULT_REPAIR_POLICY`: заполнение пропусков включено, пороги invalidation и
reconstruction — `MEDIUM`. Явные CLI-флаги могут переопределить эту политику.
Существующая команда `repair` сохраняет прежние defaults и использует тот же API.
Без явной OSM-конфигурации библиотечный `run_repair` работает без сети.

### Посмотреть и проанализировать запись

```bash
warpbuster inspect activity.fit
warpbuster analyze activity.fit --html
```

Отчёт появится рядом с исходным файлом: `activity.analyze.html`.
Для просмотра эталона на карте добавьте `--course race.gpx`; на обнаружение ошибок
это не влияет.

GPX также можно использовать как входную запись:

```bash
warpbuster inspect activity.gpx
warpbuster analyze activity.gpx --json
```

Без временных меток GPX доступна диагностика геометрии, но нельзя оценить скорость
между точками. Создание исправленного FIT из GPX не поддерживается.

### Проверить план восстановления

```bash
warpbuster repair activity.fit \
  --course race.gpx \
  --dry-run \
  --html activity.preview.html
```

`--dry-run` показывает план и причины отказов, но не создаёт FIT.

### Записать результат

```bash
warpbuster repair activity.fit \
  --course race.gpx \
  --output activity.fixed.fit \
  --html activity.repair.html
```

По умолчанию применяются изменения уровня `HIGH`. Чтобы разрешить также уровень
`MEDIUM` и заполнение отсутствующих координат:

```bash
warpbuster repair activity.fit \
  --course race.gpx \
  --fill-missing-from-course \
  --min-invalidation-confidence medium \
  --min-confidence medium \
  --output activity.fixed.fit \
  --html activity.repair.html
```

Пороги независимы:

- `--min-invalidation-confidence` определяет, какие доказанно ошибочные координаты
  можно удалить. Значения: `high` (по умолчанию) и `medium`.
- `--min-confidence` определяет, какие варианты реконструкции можно применить.
  По умолчанию `high`; кандидаты `LOW` не записываются даже при значении `low`.

`--fill-missing-from-course` разрешает заполнять исходные пропуски. Для начала
и конца тренировки предполагается совпадение с началом и концом GPX, поэтому
важно предоставить соответствующий маршрут. Такие кандидаты имеют максимум
`MEDIUM`. Для заполнения только исходных пропусков достаточно этого флага
и `--min-confidence medium`; снижать порог удаления координат не обязательно.

Без `--course` можно очистить доказанно ошибочные координаты, но не восстановить
их по маршруту. Незаполненные участки останутся пропусками.

Если применима только часть изменений, будет записан частичный результат.
Если применимых изменений нет, новый FIT не создаётся. Проверяйте отчёт перед
загрузкой результата в сервис тренировок.

### Проверить исправленный FIT

```bash
warpbuster validate activity.fixed.fit
warpbuster diff activity.fit activity.fixed.fit
```

Полезные параметры:

- `--overwrite` разрешает заменять существующие выходные FIT/HTML, но не исходники.
- `--html` без пути сохраняет отчёт рядом с записью; без флага HTML не создаётся.
- `--json` выводит машиночитаемый отчёт в stdout, в том числе вместе с `--html`.
- `-v` включает подробности; `analyze -vv` — расширенную диагностику.
- `warpbuster <команда> --help` показывает все параметры команды.

## HTML-отчёт

Отчёт открывается прямо с диска и содержит:

- карту со слоями исходного трека, результата, эталона и кандидатов, зумом
  и километровыми маркерами;
- таблицу участков с уверенностью, выполненными изменениями и причинами пропуска;
- сравнение дистанций и высот, сводку темпа и отдельные столбики набора/спуска
  на каждом километре;
- FIT diff после записи результата.

Пунктир через пропуск означает неизвестную геометрию, а не восстановленный путь.
При неполных или противоречивых данных дистанция и темп отмечаются как неопределённые.
Набор/спуск по точкам может отличаться от итогов, записанных часами.

Расчёты выполняются локально. Для карты нужен интернет: Leaflet загружается
с `unpkg.com`, подложка — с `tile.openstreetmap.org`. Эти сервисы видят IP
и область запрошенных тайлов. Сам HTML содержит координаты и телеметрию:
считайте его приватным файлом.

## Веб-интерфейс

Можно загрузить FIT и GPX через браузер и получить карту, сводку тренировки,
список изменений и исправленный файл. Веб-сервис устанавливается отдельно
и не нужен для работы CLI.

Из корня репозитория, после установки Core и активации окружения:

```bash
python -m pip install -e ./web
python -m warpbuster_web
```

Откройте [http://127.0.0.1:8000/fix](http://127.0.0.1:8000/fix).
Это локальный сервер; статическая демостраница сама по себе файлы не обрабатывает.

Веб-интерфейс использует общую с `process` политику Core: заполнение пропусков по GPX,
автоматический bounded OSM fallback и оба порога `MEDIUM`. Карта и Valhalla graph
подготавливаются общим сценарием `warpbuster.pipeline` через companion API;
пользователь не вводит graph ID и не выбирает маршрут. OSM-сбой не отменяет уже
проверенный GPX-результат. Прежние defaults команды `repair` сохранены.

Результат, включая карту и сводку, виден любому, у кого есть ссылка и доступ к серверу.
Скачать исправленный FIT может только загрузивший файлы из исходного браузера
с сохранённой cookie. Исходники и результат хранятся 7 дней.
Подробности запуска, доступа и хранения — в [документации веб-сервиса](web/README.md).

## Маршруты OpenStreetMap

Дополнительные пакеты отвечают за загрузку и кэширование OSM-данных
и построение маршрутов через Valhalla:

```bash
python -m pip install ./packages/osm-manager ./packages/osm-routing
warpbuster-osm ensure --gpx race.gpx --json > manifest.json
warpbuster-osm-route prepare manifest.json
```

Ручная подготовка выше нужна только для работы с выбранным графом; `process`
выполняет её автоматически. `prepare` возвращает `graph_id` подготовленного графа.
Подставьте его вместо `sha256:GRAPH_DIGEST` для старого CLI-сценария:

```bash
warpbuster repair activity.fit \
  --course race.gpx \
  --fill-missing-from-course \
  --osm-graph-id sha256:GRAPH_DIGEST \
  --output activity.fixed.fit \
  --html activity.repair.html
```

Команда выполняет независимую очистку, применяет пригодный GPX, затем автоматически
выбирает OSM для оставшихся внутренних разрывов с двумя достоверными anchors.
Ручной выбор не требуется. OSM сортируется по score, полной длине и route ID;
если применение не проходит проверки, проверяется следующий вариант.
Расхождение длины с записанными distance/speed учитывается как штраф для GPX/OSM,
а не как самостоятельный запрет. Если профиль телеметрии непригоден, точки
распределяются по активному времени с явной отметкой оценки; новые такие GPX-кандидаты
имеют MEDIUM. Проверки пауз и физической скорости сохраняются. Исходная правдоподобная
дистанция остаётся в FIT, даже если расходится с восстановленной геометрией.
Автоматический OSM имеет MEDIUM; это default CLI с графом, без графа остаётся HIGH.
Явный `--min-confidence high` исключает автоматический OSM. Detector не меняется.
`--dry-run` строит тот же итоговый план без записи; JSON/HTML показывают источники,
отказы и FIT diff после записи. Ошибка OSM сохраняет пригодный GPX и очистку.
Путь остаётся приближённой гипотезой; altitude/distance не выводятся из OSM.
В `process` подготовка графа автоматическая, через тот же Core-сценарий, что в вебе.
Linux web worker дополнительно включает изолированную process group с лимитами
ресурсов; локальный CLI вызывает companion API напрямую. DEM остаётся будущим этапом.

В Core включён приближённый сбор OSM-кандидатов ([010I](tasks/010i-approximate-osm-candidates.md)):
привязка допускает расстояние до 100 м, близкие проекции на одну дорогу объединяются
в пределах 20 м вдоль дороги. Контекст перед разрывом помогает ранжировать варианты,
но его неоднозначность не блокирует поиск. Проверяются до двух групп дорог на каждой
опоре и до четырёх пар, с native alternatives для каждой пары. Контекст не пересекает
пропуски и события остановки. В HTML видны маршруты, уверенность гипотезы и причины
отказа отдельных попыток; OSM-слои включены по умолчанию в preview. Это не разрешение
применять маршрут. Строгий typed routing API 010H остаётся доступен.

После совместного discovery Core ранжирует GPX и OSM по единым грубым диапазонам
([012D](tasks/012d-gap-candidate-ranking.md)): расстояниям привязки, контексту подхода,
направлению и согласованности длины с пригодными FIT distance/speed. Близкие оценки
дают `ambiguous`; прежний OSM `primary` и тип provider бонусов не имеют. Рекомендация
показывается отдельно и сама по себе не разрешает запись координат в FIT.

Загрузка OSM требует сети; построение маршрутов по подготовленному графу выполняется
локально. Настройки кэша и полный набор команд:
[OSM Manager](packages/osm-manager/README.md) и
[OSM Routing](packages/osm-routing/README.md).

## Пакетная обработка

Для нескольких пар FIT/GPX подготовьте CSV с колонками `fit,gpx`.
Пути отсчитываются от расположения CSV:

```csv
fit,gpx
tracks/run.fit,tracks/course.gpx
```

Из корня репозитория:

```bash
python scripts/repair_pairs.py pairs.csv --dry-run
python scripts/repair_pairs.py pairs.csv
```

Скрипт включает заполнение пропусков, оба порога `MEDIUM` и **перезапись выходных
файлов**. Исправленные FIT и HTML появляются рядом с исходниками, сводный отчёт —
в `pairs.reports/index.html`. Исходники не меняются; ошибка одной пары не останавливает
остальные. `--dry-run` только проверяет пути и показывает команды.
Все параметры: `python scripts/repair_pairs.py --help`.

## Ограничения и документация

WarpBuster не гарантирует восстановление каждого пропуска: неоднозначный маршрут
или противоречивые измерения могут привести к отказу. Интеграций с аккаунтами
Garmin, COROS и Strava нет — работа ведётся с экспортированными файлами.

- [Roadmap](ROADMAP.MD) — выполненные и запланированные возможности.
- [CLI](docs/CLI_SPEC.md) — подробный контракт команд.
- [Архитектура](docs/ARCHITECTURE.md) — устройство Core.
- [Модель детекции](docs/DETECTION_MODEL.md) — основания для классификации ошибок.

### Task 012B: подтверждённый OSM fallback в Core

Исторический Python API может распределять подтверждённый OSM route по исходным FIT records
и применять его обычным atomic writer после GPX. Он сохранён для совместимости;
продуктовый CLI использует автоматическую политику [012E](tasks/012e-automatic-osm-application.md).
В историческом API неподтверждённый маршрут остаётся unresolved даже при
одном Valhalla candidate: alternatives не являются исчерпывающим поиском, а
recorded speed/distance не считаются независимым доказательством выбора пути.

```python
from warpbuster.config import OSMApplicationConfig
from warpbuster.models.reconstruction import OSMRouteConfirmation
from warpbuster.reconstruction import apply_confirmed_osm_routes, osm_route_fingerprint
from warpbuster.fit.writer import write_repaired_fit

# activity, integrity, base_plan: original FIT → detector → GPX-first build_repair_plan.
# discovery: OSMReconstructionProvider(...).discover(activity, base_plan, exact_graph_id).
# gap_id/route_id: exact travelled route explicitly confirmed outside Core.
config = OSMApplicationConfig()
fingerprint = osm_route_fingerprint(
    activity, base_plan, discovery, gap_id, route_id,
    config=config, integrity_config=integrity.config,
)
# Computing a fingerprint is NOT confirmation. Present this exact snapshot for
# review, then retain the reviewed fingerprint and the actual evidence statement.
confirmation = OSMRouteConfirmation(gap_id, route_id, reviewed_fingerprint, evidence)
plan = apply_confirmed_osm_routes(
    activity, integrity, base_plan, discovery, (confirmation,), config=config,
)
result = write_repaired_fit(activity, plan)  # Standard HIGH selection; includes FIT diff.
```

`evidence` — непустое утверждение caller о фактически пройденном пути, не автоматическая
строка «найден один маршрут». Core не проверяет истинность этого утверждения.
Подтверждение не обходит отказ по timestamps, pauses, connectors, скорости или
конфликтующим plausible signals. Сохраняются timestamps, altitude и recorded distance;
неизвестная/неправдоподобная distance остаётся явно uncertain. Полный provenance
и FIT diff доступны через существующие `write_result_report` / `write_repair_html`.
Автоматическая route identity и production web integration остаются отдельной работой:
[012B](tasks/012b-confirmed-osm-core-application.md),
[012C](tasks/012c-web-hybrid-production.md).
