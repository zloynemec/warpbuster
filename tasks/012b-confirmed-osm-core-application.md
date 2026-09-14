# Task 012B — Confirmed OSM Core Application

Статус: завершена 2026-09-14 в ограниченном confirmed-route scope.
Milestone M11; после 012A.

## Ограниченный scope и решение о confidence

GPX-first Python API: после `build_repair_plan` и OSM `discover` можно применить
подтверждённый OSM route только в оставшемся internal gap. Native alternatives
неполны; один candidate, shortest route, совпадение distance/speed или отсутствие
альтернатив не доказывают route identity. Без явного подтверждения точного пути
результат остаётся unresolved при любом min-confidence. Это первый безопасный
Core-этап, НЕ автоматический OSM repair по результату эвристического ranking.

Caller передаёт `OSMRouteConfirmation`: gap ID, route ID, fingerprint и непустое
описание внешнего подтверждения пути. Fingerprint связывает исходный FIT, immutable
mask/gap, геометрию, весь сохранённый routing audit и конфигурацию применения.
Подтверждение — утверждение caller о фактически пройденном пути, а не криптографическое
доказательство: Core не проверяет его истинность и никогда не создаёт его автоматически.
Конфликтующие подтверждения оставляют gap unresolved. Route с подтверждённой identity
всё равно обязан пройти все физические и signal gates, иначе candidate не создаётся.

## Реализация

- Отдельные application config и OSM provenance; не маскировать OSM как GPX.
- Независимая проверка source/mask/gaps, eligibility и exact anchors; GPX candidate
  сохраняется независимо от application threshold. Не расширять invalidation scope.
- Валидная WGS84 polyline, ограниченные connectors, positive геодезическая длина.
- Строго возрастающие исходные timestamps; closed timer pauses исключаются из
  allocation, open pauses/zero active time → refusal.
- Allocation по qualified monotonic distance, иначе qualified speed, иначе active
  time. Каждый plausible distance/speed обязан согласоваться с длиной; противоречие
  не устраняется другим сигналом. Каждый allocated transition проверяется по
  route chainage/active time и по фактической геометрии между records.
- Не исправлять distance на основании OSM: unavailable/reset/implausible distance
  остаётся source-unverified. Уже независимые detector distance-spike repairs
  остаются прежними. Не менять timestamps, altitude, sensor/unknown/developer fields.
- Merge в обычный GapRepairPlan и один существующий atomic FIT writer с FIT diff.
- Bounded queries/points из 012A и record limit application; без O(n²) geometry search.
- JSON/console/HTML gap audit содержит provider, confirmation, routing provenance,
  allocation, distance quality и причины refusal.

## Acceptance criteria

1. Синтетический GPX+OSM plan заполняет только unresolved gap; preserved координаты,
   timestamps, sensor/unknown/developer payload неизменны; CRC/diff valid.
2. Missing, invalidated и mixed internal gaps поддержаны; prefix/suffix отвергаются.
3. Один/несколько неподтверждённых paths, конфликт/stale confirmation и GPX overlap
   не создают OSM edits, даже с LOW threshold.
4. Geometry/connectors, timing/pauses, speed, distance conflict, limits и unpatchable
   fields имеют тесты refusal; loop geometry распределяется по chainage.
5. Повтор одного snapshot/config/confirmation даёт одинаковый plan/provenance/FIT;
   fingerprint меняется при изменении FIT, route, audit или config.
6. JSON и HTML содержат OSM provenance и FIT diff; отсутствие OSM не меняет GPX flow.
7. Полный Core pytest, Ruff, mypy проходят; existing companion contracts не меняются.

## Не входит

CLI confirmation format, automatic route identity inference, DEM, acquisition/cache
orchestration, web/Docker/runtime hardening и deployment. CLI 012A остаётся dry-run.
Следующий отдельно специфицированный этап: [012C](012c-web-hybrid-production.md).

## Проверка реализации, 2026-09-14

Добавлен `reconstruction/osm_application.py` и публичные функции
`osm_route_fingerprint` / `apply_confirmed_osm_routes`. 61 новый test case в
`tests/test_osm_application.py` покрывает acceptance criteria 1–7, включая
реальный offline Valhalla graph с развилкой → confirmation → allocation → FIT writer.
Проверены synthetic sensor/developer/unknown fields и неизвестное message payload,
добавление отсутствующих coordinate fields, JSON/HTML provenance и FIT diff.
Повторное применение исходного snapshot даёт одинаковый план и идентичные FIT bytes.

Команды (Python 3.14, установленные dev dependencies и companion runtime):

```bash
PYTHONPATH=src:packages/osm-routing/src:packages/osm-manager/src python -m pytest -q
python -m ruff check src tests
PYTHONPATH=src python -m mypy src/warpbuster
(cd packages/osm-routing && PYTHONPATH=src:../osm-manager/src python -m pytest -q)
(cd packages/osm-manager && PYTHONPATH=src python -m pytest -q)
git diff --check
```

В этом worktree использован interpreter
`/Users/tkozlov/projects/warpbuster/.venv/bin/python` с явным `PYTHONPATH` на текущий
worktree. Private fixtures отсутствуют: 17 private tests skipped. Native Valhalla
integration tests исполнены. Routing: 292 passed; Manager: 56 passed;
Core: 536 passed, 17 skipped за 29.23 s.
Ruff и mypy (50 source files) проходят. Acceptance criteria 1–7 выполнены.

20 000 records / один synthetic internal gap: detector + base planner + fake
OSM discovery + fingerprint + allocation = 0.696 s (без FIT read, fixture creation,
реального routing и записи). Это проверка Core overhead, не SLA Valhalla.

Ограничения: подтверждение caller не проверяется на истинность; автоматический
выбор пути не реализован. Core API доступен, CLI application/UX ещё нет. Graph
готовится отдельно. Distance без надёжной семантики сохраняется с uncertainty;
OSM не исправляет altitude. Production-этап только специфицирован в 012C;
web/Docker/deployment не выполнялись.
