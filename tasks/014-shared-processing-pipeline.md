# 014 — Общий сценарий обработки Core / CLI / Web

## Область задачи

Перенести сбор полного сценария и политику из веба в `warpbuster`.
CLI принимает FIT и GPX и выполняет подготовку OSM, восстановление и запись FIT.
OSM coverage/cache/download/snapshot и graph preparation делегируются существующим
API `warpbuster-osm-manager` / `warpbuster-osm-routing`, без нового алгоритма этих подсистем.

## Acceptance criteria

- [x] Один библиотечный API чтения, детекции, GPX/OSM-планирования, отбора и записи.
- [x] Web использует API и общую политику, не собирает этапы восстановления.
- [x] `warpbuster process FIT GPX` готовит служебные файлы и итоговый FIT.
- [x] AUTO / OFFLINE / DISABLED, существующий prepared graph и старый `repair` поддержаны.
- [x] Общая политика полного запуска одинакова для CLI и Web, включая OSM threshold.
- [x] Сохранены GPX fallback, изоляция Linux worker, privacy projection и FIT diff.
- [x] Побайтовое равенство CLI / Web проверяется для GPX и OSM; проверены no-op,
      dry-run, отказ записи и ошибки OSM, повторное использование cache.
- [x] Предыдущие tests, применимые lint/type checks и новые tests проходят.

Изменение detector/reconstruction algorithms, разработка Manager/Router, UI redesign
и deployment не входят в этот task.


## Реализация

- `warpbuster.pipeline.run_repair` — единый file-to-result API; возвращает типизированный
  `RepairRun` с исходной/исправленной активностью, plan/selection, OSM audit и FIT diff.
- `RepairPolicy` / `DEFAULT_REPAIR_POLICY` определены в Core, immutable. Полный CLI
  и Web используют одни defaults; `repair` сохраняет совместимый legacy profile.
- `warpbuster process FIT GPX` выполняет полный сценарий. `--work-dir` задаёт кеш;
  `--osm-mode auto|offline|disabled` управляет приобретением покрытия. Явный prepared
  graph используется без acquisition. `--dry-run` не пишет FIT, `--json` содержит
  политику, план, FIT diff и provenance служебных OSM-артефактов.
- Общая OSM-обвязка вызывает `plan_from_geometry`, `OsmManager.ensure`,
  `GraphCache.prepare` и существующий typed Routing client. Алгоритмы Manager,
  Router, Integrity Detector и Reconstruction не менялись.
- Web содержит HTTP/queue/storage и allowlisted presentation; отдельные
  `warpbuster_web.osm_pipeline` / `osm_worker` удалены. Linux resource isolation
  перенесена в Core и включается конфигурацией Web.
- Optional dependency `warpbuster[osm]` объединяет companion dependencies;
  web зависит от этого extra. Оба wheels собраны и проверены.

## Новые и обновлённые проверки

- `tests/test_processing_pipeline.py`: immutable/default policy, валидация лимитов,
  общая последовательность, dry-run/no-op, однократная запись, сохранение источников,
  отказ writer, стабильные input errors, точный GPX fallback после OSM exception.
- Полный AUTO CLI проходит реальные coverage/ensure/graph/discovery/application
  с подменой только HTTP transport синтетическими OSM-данными. OFFLINE повторно
  использует cache без загрузок; prepared graph даёт идентичные байты FIT.
- Изменение порога HIGH/MEDIUM одинаково влияет на OSM selection и writer.
- Web/CLI byte parity: GPX missing endpoints, MEDIUM tail, native OSM.
- `tests/test_pipeline_boundary.py`: Web не импортирует detector/reconstruction/writer,
  Core не зависит от Web.
- Существующие privacy, Linux isolation, native routing, CLI и private tests сохранены.

## Команды проверки и результаты

```bash
.venv/bin/python -m pytest tests web/tests -q
# 757 passed, 6 skipped; 1 existing Starlette/AnyIO deprecation warning.

.venv/bin/python -m pytest tests/test_processing_pipeline.py tests/test_pipeline_boundary.py web/tests/test_config.py -q
# 74 passed после финального расширения validation/boundary tests.

(cd packages/osm-manager && ../../.venv/bin/python -m pytest -q)
# 56 passed.
(cd packages/osm-routing && ../../.venv/bin/python -m pytest -q)
# 450 passed.
node --test web/tests/*.test.mjs
# 14 passed.

.venv/bin/python -m ruff check src tests web/backend web/tests
.venv/bin/python -m mypy src/warpbuster
# Чисто; mypy — 59 source files.
git diff --check
```

Wheels Core/Web дополнительно построены через `pip wheel --no-deps --no-build-isolation`
временным каталогом назначения; проверены наличие новых Core-модулей, отсутствие
удалённых Web-модулей и зависимость Web от `warpbuster[osm]`.

## Ограничения

AUTO требует установленных companion packages и доступных OSM-данных; при отказе
сохраняется GPX fallback с диагностикой. Прямой CLI использует бюджеты companion API;
жёсткая process-group изоляция Web рассчитана на Linux. Внешний Overpass не вызывался
при проверках: использован синтетический HTTP transport с реальным остальным pipeline.
Зелёные byte-parity tests относятся к одинаковым данным, графу и repair policy;
другой snapshot или resource budget может дать другой набор доступных OSM-кандидатов.
Deployment, изменение алгоритмов детекции/восстановления и новый OSM Manager/Router
сознательно не выполнялись. Acceptance criteria этого task выполнены.
