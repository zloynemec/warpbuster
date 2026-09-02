# Task 010B — Production Snapshot Materialization + Graph Cache

Статус: завершена 2026-09-02.

## Простыми словами

Task 010A доказала, что цепочка работает:

```text
OSM Manager snapshot → один PBF → Valhalla tiles → маршрут
```

Но сейчас это эксперимент: при каждом запуске создаётся временная директория, PBF и
tiles строятся заново, а объединение перекрывающихся OSM blobs доверено готовой функции
`osmium` без полной диагностики конфликтов.

Task 010B превращает первую половину этой цепочки в надёжный production-компонент.
Команда один раз проверяет snapshot, безопасно объединяет данные и публикует готовый
Valhalla graph в локальный content-addressed cache. Следующие запуски используют его
повторно.

Эта задача **ещё не прокладывает маршрут для WarpBuster**. Она только гарантирует, что
для заданного OSM snapshot существует проверенный и воспроизводимый graph artifact.

## Пользовательский результат

Основной сценарий:

```bash
warpbuster-osm-route prepare /absolute/path/to/manifest.json
```

Первый запуск:

1. проверяет immutable manifest OSM Manager и hashes source files;
2. объединяет пересекающиеся OSM blobs;
3. диагностирует duplicates, versions, conflicts и оборванные ways;
4. создаёт canonical derived PBF;
5. строит Valhalla tiles;
6. атомарно публикует graph cache;
7. возвращает стабильный `graph_id` и полный provenance.

Повторный запуск с тем же snapshot, engine и build config:

1. находит тот же `graph_id`;
2. проверяет опубликованные artifacts;
3. возвращает `CACHED` без повторного merge и запуска `valhalla_build_tiles`.

## Цель

Реализовать production boundary между immutable cache OSM Manager и Valhalla:

- source snapshot всегда остаётся read-only;
- overlapping blobs объединяются детерминированно и с явной диагностикой;
- неполный или конфликтующий dataset не превращается в graph;
- derived PBF/config/tiles публикуются только полным атомарным набором;
- concurrent процессы не строят и не перезаписывают один graph одновременно;
- каждый artifact проверяем и однозначно связан с source snapshot и версиями runtime;
- повторная подготовка использует cache и не выполняет network requests.

## Архитектурная граница

Derived graph cache принадлежит `warpbuster-osm-routing`, а не OSM Manager:

```text
OSM Manager cache                     OSM Routing cache
-----------------                     -----------------
raw immutable blobs  ──read-only──→   canonical PBF
snapshot manifest                     Valhalla config
coverage/freshness                     Valhalla tiles
Overpass/import                        graph manifest
```

Причина разделения:

- Manager отвечает за получение и версионирование исходных OSM data;
- routing package отвечает за engine-specific derived artifacts;
- обновление Valhalla или build profile не создаёт новый source snapshot;
- удаление derived graph безопасно: его можно восстановить из snapshot.

В Task 010B запрещено добавлять imports из `warpbuster` или
`warpbuster_osm_manager`. Интеграция выполняется только через protocol v1 manifest и
referenced files.

## Ожидаемые изменения в package

Ориентировочно будут затронуты:

```text
packages/osm-routing/
  README.md
  pyproject.toml
  osm-routing.example.toml
  src/warpbuster_osm_routing/
    cli.py
    config.py              # новый typed config contract
    manifest.py
    normalize.py           # новый bounded merge/conflict detector
    materialize.py
    graph_cache.py         # новый cache/locking/atomic publication layer
    models.py
    valhalla_backend.py
  tests/
```

Core, FIT reader/writer, Integrity Detector, reconstruction и OSM Manager не меняются.

## Входной contract

Принимается только manifest OSM Manager:

- `protocol_version=1`;
- `manifest_version=1`;
- `dataset_profile=pedestrian-routing-v1`;
- абсолютные paths к immutable data files;
- `.osm`, `.osm.gz`, `.osm.xml.gz` и `.osm.pbf` согласно declared media type;
- declared size и SHA-256 каждого файла.

До разбора OSM objects проверяются manifest, версия/profile, file count, paths, sizes и
hashes. Ошибка любого source file отменяет всю подготовку.

Bare `.osm`/`.pbf` не становятся production-входом routing package. Их сначала
импортирует OSM Manager, чтобы сохранить snapshot/provenance contract.

## Детерминированное объединение OSM

### Canonical object payload

Для сравнения и semantic digest используются данные, влияющие на graph:

- node: type, ID, version, visibility, latitude/longitude и sorted tags;
- way: type, ID, version, visibility, ordered node references и sorted tags;
- relation: type, ID, version, visibility, ordered typed members/roles и sorted tags.

Author metadata (`user`, `uid`, `changeset`) не входит в routing semantic digest и не
копируется в canonical PBF. Исходные metadata остаются в immutable raw snapshot.

### Duplicate/version policy

Для каждого `(object type, OSM ID)`:

- одинаковая version и одинаковый canonical payload — exact duplicate, оставить один;
- одинаковая version и разный payload — `OBJECT_VERSION_CONFLICT`, отказ от всего
  graph;
- разные versions — детерминированно выбрать наибольшую version и посчитать заменённые
  старые versions;
- highest version с `visible=false` становится tombstone и не попадает в PBF;
- одинаковый logical input не зависит от порядка files/objects.

После merge:

- каждый node reference видимого way обязан разрешаться в видимый node;
- way с отсутствующей node даёт `UNRESOLVED_WAY_REFERENCE`, а не урезанную geometry;
- relations, если они присутствуют в imported PBF, сохраняются и учитываются в
  statistics;
- unresolved relation members считаются отдельно и публикуются как warning, потому что
  bounded OSM extracts могут законно содержать неполные relations;
- OSM change/history files не поддерживаются и отклоняются явно.

Нельзя хранить все пары объектов и сравнивать их наивным O(n²) алгоритмом. Реализация
использует streaming/external sort либо indexed temporary storage с bounded memory.

## Два разных hash

Нужно не смешивать два понятия.

### Semantic object digest

SHA-256 стабильной canonical serialization выбранных nodes/ways/relations. Он доказывает,
что после merge получился тот же логический OSM dataset независимо от input order и
формата XML/PBF.

### Artifact hashes

SHA-256 готового PBF, config и дерева tiles проверяет целостность конкретных файлов.
Byte-identical tiles между разными версиями Valhalla не обещаются.

Semantic compatibility определяется source hashes, materializer schema, engine version
и build profile, а не совпадением случайных абсолютных paths или времени запуска.

## `graph_id` и cache key

`graph_id` имеет вид `sha256:<64 lowercase hex>` и вычисляется до тяжёлой сборки из
canonical JSON со следующими полями:

- cache key schema version;
- OSM `snapshot_id`;
- dataset profile;
- упорядоченные source file SHA-256;
- materializer schema/version;
- `osmium`/libosmium runtime version;
- Valhalla version;
- build profile ID;
- semantic build config hash.

В key запрещено включать:

- absolute paths;
- cache/work directory;
- PID;
- timestamps запуска;
- порядок source files в manifest;
- logging verbosity.

Изменение Valhalla, materializer или build profile создаёт новый `graph_id` и не
переинтерпретирует старый graph молча. Request-time trail profile не входит в graph
identity и проверяется отдельно.

## Структура derived cache

Platform-native default можно переопределить `--cache-dir`, TOML или environment:

```text
osm-routing-cache/
  graphs/
    <graph-id-without-prefix>/
      manifest.json
      source.osm.pbf
      valhalla.json
      tiles/
  locks/
    <graph-id-without-prefix>.lock
  staging/
```

Graph manifest содержит:

- protocol/manifest/cache schema versions;
- `graph_id`, `snapshot_id` и source manifest SHA-256;
- ordered source hashes и OSM base timestamp;
- object/duplicate/version/relation/reference statistics;
- semantic object digest;
- materializer, osmium и Valhalla versions;
- build profile/config hashes;
- PBF size/hash;
- число, общий size и tree digest tiles;
- build timings и warnings;
- OSM attribution/license из source manifest;
- состояние `READY`.

Manifest не содержит FIT/GPX telemetry или исходное имя пользовательского файла.

## Atomic publication и concurrent build

Для одного `graph_id` используется отдельный bounded lock.

Алгоритм:

1. вычислить graph key;
2. если READY graph существует — полностью проверить и вернуть `CACHED`;
3. получить lock либо завершиться `LOCK_TIMEOUT`;
4. повторно проверить cache после получения lock;
5. строить PBF/config/tiles только в уникальной staging directory на том же filesystem;
6. проверить все outputs и записать manifest последним;
7. атомарно переименовать staging directory в final graph directory;
8. освободить lock.

Build failure, interruption или превышение limit не публикуют частичный graph. Staging
можно удалить, а source snapshot никогда не изменяется.

Если concurrent process успел опубликовать тот же валидный graph, второй процесс
проверяет и переиспользует его. Существующий несовместимый или повреждённый artifact не
перезаписывается молча.

## Cache verification и corruption policy

Cache hit обязан проверить:

- graph manifest/schema/key;
- source snapshot identity и source hashes;
- PBF size/hash;
- semantic config hash;
- полный tile tree: relative paths, sizes и SHA-256/tree digest;
- отсутствие paths, выходящих за graph directory.

Повреждение возвращает `CACHE_CORRUPT`. Восстановление требует явного `--rebuild` и
создаёт новый staging artifact до замены exact graph directory. Повреждённые source OSM
files нельзя «лечить» rebuild-ом — сначала должен снова пройти source manifest check.

## CLI

### Подготовка

```bash
warpbuster-osm-route prepare /path/to/manifest.json
warpbuster-osm-route prepare /path/to/manifest.json --cache-dir /path/to/cache
warpbuster-osm-route prepare /path/to/manifest.json --rebuild
warpbuster-osm-route prepare /path/to/manifest.json --json
```

Console/JSON output содержит как минимум:

- status `READY` или `CACHED`;
- `graph_id` и абсолютный path к graph manifest;
- snapshot/materializer/engine/profile provenance;
- source, PBF и tiles sizes/hashes;
- object merge statistics;
- warnings;
- cache hit/build timings;
- применённые resource limits.

### Диагностика и обслуживание

```bash
warpbuster-osm-route list
warpbuster-osm-route inspect GRAPH_ID
warpbuster-osm-route remove GRAPH_ID
warpbuster-osm-route prune --dry-run
warpbuster-osm-route prune --apply
```

- `inspect` выполняет полную проверку exact graph;
- `remove` принимает только exact validated graph ID;
- `prune` по умолчанию ничего не удаляет;
- `--apply` удаляет только перечисленные derived artifacts;
- active/locked graph не удаляется;
- команды никогда не удаляют OSM Manager snapshots/blobs.

## Typed configuration и defaults

Все limits находятся в `RoutingCacheConfig`, документируются в
`osm-routing.example.toml` и покрываются tests.

| Имя | Единицы | Default | Назначение |
|---|---:|---:|---|
| `maximum_manifest_bytes` | bytes | 1 MiB | bounded JSON input |
| `maximum_data_files` | files | 64 | source fan-in |
| `maximum_total_source_bytes` | bytes | 2 GiB | raw snapshot input |
| `maximum_osm_objects` | objects | 5 000 000 | nodes + ways + relations |
| `maximum_total_node_references` | refs | 50 000 000 | protection from pathological ways |
| `maximum_total_tag_bytes` | bytes | 512 MiB | bounded retained tags |
| `maximum_output_pbf_bytes` | bytes | 2 GiB | canonical PBF output |
| `maximum_tile_files` | files | 250 000 | Valhalla output fan-out |
| `maximum_total_tile_bytes` | bytes | 8 GiB | derived graph size |
| `build_timeout_seconds` | seconds | 900 | bounded external build |
| `cache_lock_timeout_seconds` | seconds | 60 | concurrent wait |
| `stale_lock_seconds` | seconds | 1 800 | abandoned lock diagnostics |
| `io_chunk_bytes` | bytes | 1 MiB | hashing/copy streaming |

Это safety ceilings, а не ожидаемые размеры конкретной гонки. Изменение default требует
документации и regression test; algorithms не содержат локальных числовых thresholds.

Приоритет configuration:

```text
CLI override → explicit/auto osm-routing.toml → environment → defaults
```

## Stable error codes

Минимальный набор:

- `MANIFEST_INVALID`;
- `UNSUPPORTED_PROTOCOL` / `UNSUPPORTED_DATASET_PROFILE`;
- `SOURCE_MISSING` / `SOURCE_SIZE_MISMATCH` / `SOURCE_HASH_MISMATCH`;
- `UNSUPPORTED_OSM_FORMAT`;
- `RESOURCE_LIMIT_EXCEEDED`;
- `OBJECT_VERSION_CONFLICT`;
- `UNRESOLVED_WAY_REFERENCE`;
- `PBF_MATERIALIZATION_FAILED`;
- `VALHALLA_BUILD_FAILED` / `BUILD_TIMEOUT`;
- `LOCK_TIMEOUT`;
- `CACHE_CORRUPT`;
- `OUTPUT_EXISTS` для несовместимого final artifact;
- `UNSAFE_CACHE_TARGET` для broad/invalid destructive target.

Console mode не показывает traceback для ожидаемых ошибок. JSON всегда возвращает code,
message и измеримые details.

## Тесты

### Manifest/source boundary

- все поддерживаемые OSM media types;
- malformed/oversized manifest;
- relative/missing source path;
- size/hash mismatch;
- unsupported versions/profile;
- source/data-file limits.

### Merge semantics

- exact duplicate across overlapping blobs;
- same-version identical object in XML и PBF;
- deterministic highest-version selection;
- same-version different node coordinate/tag/ref → conflict;
- tombstone handling;
- missing way node reference → отказ;
- incomplete relation member → counted warning;
- shuffled files/objects → одинаковый semantic digest и PBF semantics;
- no O(n²) behavior on bounded large synthetic fixture.

### Graph cache

- first prepare builds and publishes READY graph;
- second prepare is CACHED and не запускает Valhalla builder;
- изменение snapshot/engine/profile/config меняет graph ID;
- разные absolute cache paths не меняют graph ID;
- build failure/timeout не оставляет published graph;
- PBF/config/tile corruption обнаруживается;
- `--rebuild` заменяет только exact graph после успешной новой сборки;
- два concurrent prepare публикуют один graph;
- stale lock имеет bounded diagnostics;
- list/inspect/remove/prune и защита broad targets.

### Integration

- полностью offline synthetic graph build;
- optional ignored private Andromeda snapshot acceptance;
- wheel install и CLI smoke;
- отсутствие imports из Core/Manager;
- Ruff, format и strict mypy.

Live Overpass tests не входят: 010B получает уже опубликованный snapshot.

## Acceptance criteria

Task 010B завершена, если:

- valid Manager snapshot создаёт один READY graph с полным manifest/provenance;
- повторный вызов возвращает тот же `graph_id` как CACHED без merge/build;
- input order/path не меняют semantic digest и graph ID;
- conflicts и incomplete ways никогда не дают частично успешный graph;
- PBF и tiles публикуются атомарно и обнаруживают последующее повреждение;
- concurrent build безопасен и bounded;
- все resource ceilings именованы, configurable и протестированы;
- cache maintenance не может затронуть Manager cache или broad directory;
- package tests, Ruff, format, strict mypy и wheel smoke зелёные;
- предыдущие Core, OSM Manager и Task 010A tests остаются зелёными;
- нет routing query API, FIT, detection или reconstruction изменений.

## Спорные решения, зафиксированные в ТЗ

1. **Cache находится в routing package.** Tiles зависят от Valhalla, а не от способа
   получения raw OSM.
2. **Конфликт одной OSM version — hard failure.** Выбрать один payload означало бы
   скрыть нецелостный snapshot.
3. **Высшая version выбирается детерминированно.** Это необходимо для перекрывающихся
   cells, загруженных в разное время; все замены видны в diagnostics.
4. **Graph ID не равен tile hash.** Он описывает semantic build inputs; artifact hashes
   отдельно защищают конкретные files.
5. **Relations не интерпретируются в 010B.** Они сохраняются и диагностируются;
   trail/access policy относится к 010C.
6. **Cache corruption не чинится молча.** Автоматическое использование сомнительного
   graph опаснее явного rebuild.

## Сознательно не реализуется

- pedestrian/trail policy и настройка route cost;
- snapping thresholds и anchor ambiguity;
- single-route/alternative-route production API;
- выбор пути по GPX/FIT telemetry;
- OSM-backed reconstruction;
- FIT writer, HTML report или map rendering;
- обновление/refresh source OSM data;
- daemon или HTTP service.

## Фактическая реализация и проверка

Реализованы:

- `RoutingCacheConfig` с platform-native default, CLI/TOML/environment priority и всеми
  именованными limits;
- SQLite-backed streaming normalizer с canonical payload, exact duplicate accounting,
  highest-version selection, tombstones, hard conflict failure и reference checks;
- path-independent `graph_id`, учитывающий source hashes, materializer schema,
  pyosmium/libosmium, Valhalla и semantic build config;
- per-graph exclusive lock, unique staging, atomic publication и rollback-safe rebuild;
- полный graph manifest с source/license provenance, merge statistics, semantic/artifact
  hashes, timings, warnings и applied limits;
- полная проверка PBF, relocatable semantic config и каждого файла tile tree на cache hit;
- CLI `prepare`, `list`, `inspect`, `remove`, `prune --dry-run/--apply` и сохранённый
  diagnostic `spike` Task 010A;
- packaged `osm-routing.example.toml` и clean-wheel CLI smoke.

Synthetic suite содержит 37 tests, в том числе XML/XML.gz/PBF inputs, duplicate/version
semantics, conflicts, tombstones, incomplete ways/relations, deterministic identity,
resource limits, corruption каждого artifact type, failed/timeout/rebuild atomicity,
concurrent prepare, stale locks, maintenance и настоящий Valhalla build/load.

Private Andromeda snapshot acceptance:

- snapshot `sha256:5db893b562109bb2387e03205458e481f4d81a1d9aedda4b2e5dddf00efc02bf`;
- 28 463 input objects, 1 174 exact overlaps, 25 444 selected nodes и 1 845 ways;
- canonical PBF 191 499 bytes, 4 Valhalla tiles / 799 728 bytes;
- первый запуск `READY` за 1,78 s, повторный `CACHED` за 0,21 s;
- стабильный graph ID
  `sha256:8cf025456deca7fea8fd1c1a4db143fc6f18ef13ea37145cfb6070ac6ca36c68`.

Команды проверки:

```bash
cd packages/osm-routing
../../.venv/bin/pytest -q
../../.venv/bin/ruff check .
../../.venv/bin/ruff format --check .
../../.venv/bin/mypy src
../../.venv/bin/python -m build .
```

Regression: WarpBuster Core — 218 passed, 5 skipped; OSM Manager — 56 passed. Все
acceptance criteria Task 010B выполнены.

Известные ограничения production cache: stale locks диагностируются, но не удаляются
автоматически; cache hit намеренно перечитывает и хеширует полное дерево tiles; перенос
готовой graph directory вручную считается corruption и требует rebuild в новом cache.
