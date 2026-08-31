# WarpBuster Core

WarpBuster — локальное Python-ядро и CLI для обнаружения и восстановления физически
невозможных GNSS/GPS-данных. FIT остаётся главным lossless форматом, а FIT и GPX можно
использовать как входные активности для inspection и detection.

Главная цель первой версии — **не «сделать красивый трек»**, а сначала доказать, что конкретный участок координат физически недостоверен, и только после этого разрешать реконструкцию.

## Главный принцип

> **Never modify plausible movement. Repair only demonstrably impossible GNSS data.**

Если бегун последовательно ушёл с маршрута на километры, развернулся, сделал петлю или побежал по незнакомой тропе — это настоящий трек и WarpBuster не должен его исправлять.

## Scope v0.1

В v0.1 входят:

- чтение FIT;
- чтение GPX activity без конвертации в FIT;
- нормализованная модель активности;
- CLI `inspect`, `analyze`, позже `repair` и `validate`;
- поиск физически невозможных переходов;
- обнаружение длительных spoofing islands;
- advisory-предупреждения о возможных интерполированных GNSS gaps;
- confidence/reasons для каждого подозрительного интервала;
- опциональная реконструкция только уже доказанно повреждённых интервалов по известному GPX course;
- сохранение исходных timestamps и спортивной телеметрии;
- FIT validation/diff;
- console/JSON/HTML-отчёты.

Не входят:

- Garmin/COROS/Strava API;
- OAuth/webhooks;
- web UI;
- PostgreSQL/Redis;
- OSM routing;
- DEM;
- облачная синхронизация.

## Документы

- `AGENTS.md` — обязательные правила для Codex.
- `docs/PRODUCT_SPEC.md` — функциональное ТЗ v0.1.
- `docs/ARCHITECTURE.md` — архитектура ядра.
- `docs/DETECTION_MODEL.md` — модель Integrity Detector.
- `docs/CLI_SPEC.md` — CLI-контракт.
- `docs/TEST_STRATEGY.md` — тестирование и acceptance fixtures.
- `docs/DECISIONS.md` — зафиксированные архитектурные решения.
- `docs/MILESTONES.md` — порядок разработки.
- `tasks/` — задания, которые нужно отдавать Codex **строго по одному**.

## Как работать с Codex

Не просить Codex «реализовать WarpBuster v0.1».

Нужно брать следующий незавершённый файл из `tasks/` и давать его как самостоятельное задание. После выполнения:

1. проверить acceptance criteria;
2. запустить тесты;
3. просмотреть diff;
4. зафиксировать результат;
5. только затем переходить к следующему этапу.

Первый этап: `tasks/001-project-bootstrap.md`.

## Разработка

Требуется Python 3.14 или новее.

```bash
git clone https://github.com/zloynemec/warpbuster.git
cd warpbuster
python3.14 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

Проверка CLI и импорта:

```bash
warpbuster --version
python -c "import warpbuster; print(warpbuster.__version__)"
```

Чтение и инспекция FIT:

```bash
warpbuster inspect activity.fit
warpbuster inspect activity.fit --json
warpbuster inspect activity.gpx
warpbuster inspect activity.gpx --json
```

Локальный анализ физических переходов:

```bash
warpbuster analyze activity.fit
warpbuster analyze activity.fit -v
warpbuster analyze activity.fit -vv
warpbuster analyze activity.fit --json
warpbuster analyze activity.gpx
warpbuster analyze activity.gpx --json
```

Exit code `1` означает, что найдены `SUSPICIOUS` или `IMPOSSIBLE` переходы;
нечитаемый FIT возвращает `2`.

`analyze` выбирает thresholds по нормализованному виду активности. Для `running`
используется отдельный консервативный профиль; неизвестный sport не получает
`CORRUPTED / HIGH` только из-за высокой apparent speed.

`-v` показывает стадии pipeline, а `-vv` дополнительно объясняет активные thresholds,
границы bounded island search и результаты проверки bridge-кандидатов. Детали
кандидатов ограничены конфигурацией; полные агрегатные счётчики остаются в JSON.

`analyze` также показывает `LOW` geometry warnings для длинных почти идеально прямых
участков, похожих на интерполяцию. Такое предупреждение не меняет integrity status или
exit code, не создаёт corrupted interval и всегда имеет `repair_eligible=false`.

Полный набор проверок:

```bash
python -m pytest
python -m ruff check .
python -m ruff format --check .
python -m mypy src tests
```

На текущем этапе реализованы чтение FIT, инспекция, локальный анализ соседних GNSS
observations, GPX activity input, bounded-поиск spoofing islands по impossible entry/exit
и plausible bridge, geometry gap diagnostics, а также false-positive regressions и
bounded diagnostics.
Reconstruction и repair намеренно отложены до следующих milestones согласно
`docs/MILESTONES.md`.
