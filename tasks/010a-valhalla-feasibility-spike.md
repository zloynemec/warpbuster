# Task 010A — Valhalla Feasibility Spike

Статус: завершена 2026-09-02; решение — **GO**.

## Контекст

Первоначальная декомпозиция M9 предполагала собственные OSM reader, access profile,
graph, spatial index, snapping и shortest-path search. До начала этой работы необходимо
было проверить, может ли готовый offline engine закрыть routing-задачи без ухудшения
детерминизма, provenance и архитектурной изоляции WarpBuster.

Valhalla проверяется только как routing backend после Integrity Detector. OSM path не
является доказательством GNSS corruption и не участвует в детекции.

## Цель

Экспериментально подтвердить или опровергнуть, что pinned `pyvalhalla`:

- устанавливается на Python 3.14/macOS ARM64 без сборки или patching;
- получает immutable snapshot OSM Manager через изолированный adapter;
- строит локальный pedestrian graph и выполняет запросы без network;
- возвращает auditable snapping, geometry и OSM provenance;
- даёт воспроизводимый semantic result для одинаковых входов;
- структурированно отказывает вне graph coverage.

Результат 010A — feasibility evidence и экспериментальный adapter. Это не production
API для M10 и не разрешение использовать экспериментальные defaults при FIT repair.

## Архитектурная граница

Добавлен отдельный distribution:

```text
packages/osm-routing/
  pyproject.toml
  README.md
  src/warpbuster_osm_routing/
  tests/
```

Он:

- читает публичный protocol v1 manifest без imports из Core или OSM Manager;
- проверяет manifest, sizes и SHA-256 raw OSM files;
- детерминированно материализует manager blobs в один derived PBF;
- строит Valhalla tiles локально;
- выполняет `locate`, `route` и `trace_attributes` с pedestrian costing;
- сохраняет snapshot/engine/profile/config provenance и измеримые timings;
- не читает FIT, не запускает detector и не изменяет activity.

## Проверяемая команда

```bash
warpbuster-osm-route spike \
  /absolute/path/to/manifest.json \
  --work-dir /tmp/warpbuster-valhalla-spike \
  --from 44.592092,33.766095 \
  --to 44.590376,33.777446 \
  --alternates 2 \
  --overwrite \
  --json
```

Work directory содержит только derived PBF/config/tiles. Snapshot Manager остаётся
read-only. Повторное использование output требует явного `--overwrite`.

## Критерии и фактический результат

| Критерий | Результат |
|---|---|
| Python 3.14 + macOS ARM64 wheel | PASS: `pyvalhalla 3.8.3`, CPython abi3 ARM64 wheel |
| Manager snapshot → PBF → tiles | PASS |
| Offline pedestrian route | PASS |
| Snapping diagnostics | PASS: coordinate, distance, edge/way/node provenance |
| Route audit | PASS: geometry, length, edge IDs, OSM way IDs, surface/use/sac_scale |
| Same-input repeatability | PASS для PBF hash, snapping, geometry hash и edge sequence |
| Structured outside-coverage failure | PASS |
| Valhalla fork/patch | не требуется |

### Реальный Andromeda probe

Проверен immutable snapshot
`sha256:5db893b562109bb2387e03205458e481f4d81a1d9aedda4b2e5dddf00efc02bf`
на anchors курса около 6 и 7 км:

- source files: 2 manager-generated `.osm.xml.gz`;
- materialized PBF: 256 737 bytes;
- Valhalla tiles: 4 files, 799 728 bytes;
- manifest verification: 0,002 s;
- PBF materialization: 0,102 s;
- tile build: 0,432 s;
- locate/route/audit: 0,025 s;
- total: 0,561 s;
- returned route: 0,985 km, OSM way `551192257`;
- start snap: 8,6 m;
- end snap: 69,6 m;
- requested alternatives: 2, returned routes: 1 — отсутствие реальной альтернативы
  не считается ошибкой.

Повторная независимая сборка дала одинаковые PBF SHA-256, route geometry SHA-256,
snapped candidates, audited edges и размер tiles.

## Решение

**GO:** Valhalla принимается как основной кандидат routing backend для M9.

OSM Manager сохраняется как acquisition/cache/snapshot/provenance layer. Valhalla
заменяет собственную реализацию OSM access graph, spatial index, snapping и route
search. Между ними остаётся отдельный adapter и derived-artifact cache.

## Известные ограничения spike

- `osmium.MergeInputReader(..., simplify=True)` даёт стабильный результат при
  фиксированном порядке файлов, но spike пока отдельно не диагностирует конфликтующие
  объекты одной версии из разных blobs;
- byte stability внутреннего binary tile format Valhalla пока не является contract;
  semantic cache key включает snapshot, Valhalla version, profile и config hash;
- максимальная допустимая snap distance ещё не выбрана и не применяется автоматически;
- `pedestrian` defaults ещё не являются WarpBuster trail profile;
- alternatives проверены на уровне API, но diversity/overlap policy не определена;
- экспериментальный CLI вызывает bundled Valhalla executable напрямую, не полагаясь на
  активированный shell PATH;
- relations отсутствуют в `pedestrian-routing-v1`; необходимость нового dataset profile
  будет проверяться отдельно.

## Тесты

- protocol/manifest/hash boundary;
- deterministic PBF materialization;
- synthetic pedestrian graph build;
- locate/route/trace OSM provenance;
- structured outside-coverage failure;
- CLI machine-readable error;
- Ruff, format и strict mypy.

Команды:

```bash
cd packages/osm-routing
../../.venv/bin/pytest -q
../../.venv/bin/ruff check .
../../.venv/bin/ruff format --check .
../../.venv/bin/mypy src
```

## Сознательно не реализовано

- production graph cache и locking;
- stable routing request/response protocol для M10;
- утверждённый pedestrian/trail costing profile;
- snap confidence/ambiguity policy;
- overlap/diversity ranking альтернатив;
- вызов из WarpBuster Core;
- FIT repair или HTML rendering.
