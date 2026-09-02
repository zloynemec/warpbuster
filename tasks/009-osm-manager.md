# Task 009 — OSM Manager

Статус: завершена 2026-09-02.

## Контекст

Будущая OSM-backed reconstruction требует воспроизводимого набора сырых OpenStreetMap
данных вокруг известного маршрута или явно заданной области. WarpBuster Core не должен
самостоятельно управлять Overpass endpoints, сетевыми retry, cache cells, обновлением и
локальным хранением OSM snapshots.

OSM Manager создаётся как отдельный устанавливаемый подпроект с собственным CLI. Он
управляет только получением, проверкой, версионированием и локальным cache OSM data.
Detection, routing, course matching и FIT repair остаются за пределами этой задачи.

## Цель

Реализовать отдельную команду `warpbuster-osm`, которая по запросу географического
покрытия:

- определяет требуемую область из GPX, GeoJSON или явного bounding box;
- проверяет локальный cache;
- программно загружает через Overpass только недостающие или явно обновляемые области;
- атомарно сохраняет immutable OSM snapshots и metadata;
- возвращает стабильный machine-readable manifest для будущего вызова из WarpBuster;
- после первой успешной загрузки способна обслужить тот же запрос полностью offline.

Основной пользовательский сценарий не требует ручного скачивания `.osm`/`.osm.pbf`:

```bash
warpbuster-osm ensure --gpx race.gpx
```

## Архитектурная граница

OSM Manager отвечает за:

- GPX/GeoJSON/bbox coverage input;
- безопасное вычисление buffered coverage;
- cache cells и проверку полного покрытия;
- Overpass HTTP client;
- retry/backoff и stale-cache fallback;
- immutable raw OSM blobs и snapshot manifests;
- cache inspection, refresh, import, remove/prune и diagnostics;
- integrity checks, hashes, locks и atomic publication;
- OSM provenance и license metadata;
- versioned CLI JSON protocol.

OSM Manager не отвечает за:

- чтение FIT;
- Integrity Detector;
- определение corrupted intervals;
- trusted anchors;
- pedestrian routing и shortest/k-shortest path;
- map matching GPX course на OSM graph;
- reconstruction confidence;
- coordinate allocation;
- FIT writer, validation, diff или HTML repair report;
- raster/vector tile rendering.

Manager не импортирует `warpbuster` и не зависит от внутренних моделей WarpBuster Core.
Будущая интеграция выполняется через versioned JSON protocol и immutable snapshot files.

## Структура подпроекта

На первом этапе подпроект находится в том же repository, но является отдельным Python
distribution:

```text
packages/
  osm-manager/
    pyproject.toml
    src/
      warpbuster_osm_manager/
    tests/
```

Требования:

- отдельный `pyproject.toml`;
- Python 3.14+;
- console entry point `warpbuster-osm`;
- собственные runtime dependencies;
- собственные pytest, Ruff, format и mypy команды;
- отсутствие Python imports из `src/warpbuster`;
- возможность отдельно собрать и установить wheel;
- protocol compatibility не зависит от совместного process memory.

Вынос в отдельный repository или запуск постоянного daemon не входят в Task 009.

## CLI

### `ensure`

Команда гарантирует, что cache содержит допустимый snapshot для требуемой области:

```bash
warpbuster-osm ensure --gpx race.gpx
warpbuster-osm ensure --gpx race.gpx --buffer-m 1500
warpbuster-osm ensure --geojson corridor.geojson
warpbuster-osm ensure --bbox 33.60,44.40,33.92,44.50
warpbuster-osm ensure --gpx race.gpx --offline
warpbuster-osm ensure --gpx race.gpx --refresh
warpbuster-osm ensure --gpx race.gpx --max-age 30d
warpbuster-osm ensure --request request.json --json
```

Ровно один coverage source обязателен:

- `--gpx PATH`;
- `--geojson PATH`;
- `--bbox WEST,SOUTH,EAST,NORTH`;
- `--request PATH` для полного protocol request.

Общие параметры:

- `--buffer-m METRES` — геодезический buffer вокруг line/point geometry;
- `--max-age DURATION` — допустимый возраст cached OSM data;
- `--offline` — запрет любых network requests;
- `--refresh` — принудительное создание нового snapshot;
- `--require-fresh` — запрет stale fallback;
- `--cache-dir PATH` — явное расположение cache;
- `--overpass-url URL` — явный endpoint;
- `--json` — protocol response в stdout.

`--offline` и `--refresh` несовместимы. `--require-fresh` в offline mode разрешён только
при наличии достаточно свежего полного покрытия.

Duration принимает целое положительное число с единицей `s`, `m`, `h` или `d`.

### Управление cache

```bash
warpbuster-osm list
warpbuster-osm inspect SNAPSHOT_ID
warpbuster-osm refresh --gpx race.gpx
warpbuster-osm import region.osm.pbf
warpbuster-osm remove SNAPSHOT_ID
warpbuster-osm prune --dry-run
warpbuster-osm prune --apply
warpbuster-osm doctor
warpbuster-osm capabilities --json
```

Требования:

- `list` показывает snapshots, coverage, freshness, размер и provenance;
- `inspect` показывает manifest и проверяет доступность всех referenced blobs;
- `refresh` эквивалентен `ensure --refresh`;
- `import` принимает `.osm`, `.osm.gz` и `.osm.pbf`, валидирует и помещает данные в
  тот же immutable cache;
- `remove` принимает только точный snapshot ID и не использует broad path/glob;
- `prune` по умолчанию является dry-run, реальное удаление требует `--apply`;
- `doctor` проверяет cache directory, locks, schema versions и опционально Overpass;
- `capabilities` возвращает protocol и поддерживаемые input/data formats.

Удаление cache допустимо, потому что данные можно получить повторно, но команда обязана
явно перечислить удалённые snapshots/blobs и объём. Shared blobs удаляются только когда
на них больше не ссылается ни один snapshot.

## `ensure --gpx`

### Поддерживаемая GPX geometry

Manager локально читает GPX 1.0/1.1 и использует:

- `trk/trkseg/trkpt`;
- `rte/rtept`.

Поддерживаются XML namespaces и несколько tracks/routes/segments. Для coverage берётся
union всех continuous polylines. `wpt`, timestamps, elevation, extensions, creator и
названия не участвуют в вычислении области.

Требования безопасности:

- GPX должен содержать хотя бы одну line geometry с двумя валидными coordinates;
- latitude/longitude проверяются до вычисления cells;
- XML entities и внешние resources не загружаются;
- antimeridian crossing не превращается в почти глобальный bounding box;
- один повреждённый coordinate не должен молча создавать чрезмерную область;
- превышение configurable limits даёт отказ с измеримыми diagnostics;
- исходный GPX не копируется в cache и не отправляется в Overpass.

### Вычисление покрытия

1. Прочитать continuous GPX polylines.
2. Построить geodesic buffer `gpx_corridor_buffer_m`.
3. Определить стабильный набор geographic cache cells, пересекающих corridor.
4. Проверить площадь, количество cells и ожидаемый query scope.
5. Найти cells, уже доступные с требуемой freshness.
6. Объединить недостающие соседние cells в bounded Overpass requests.
7. После загрузки вернуть snapshot, покрывающий весь requested corridor.

Нельзя использовать общий bbox всего GPX как единственный fetch scope для длинного,
извилистого или кольцевого course: это может скачать значительно больше данных, чем
необходимо. Bounding box допустим только как preliminary safety metric и для явно
заданного `--bbox`.

## GeoJSON и bbox

`--geojson` принимает один `Feature`, `FeatureCollection` или bare geometry с типами:

- `Point`/`MultiPoint`;
- `LineString`/`MultiLineString`;
- `Polygon`/`MultiPolygon`.

Properties игнорируются. Coordinates интерпретируются как WGS84 longitude/latitude.
Line/point geometry получает `--buffer-m`; polygon geometry уже задаёт coverage, но
может быть дополнительно расширена явным buffer.

`--bbox` использует порядок `WEST,SOUTH,EAST,NORTH`. Antimeridian-spanning bbox должен
быть представлен и обработан явно, без неявного выбора почти глобальной области.

## Dataset profile v1

Task 009 загружает сырые данные, достаточные для будущего pedestrian/trail routing, но
сам route graph не строит.

Versioned profile `pedestrian-routing-v1` должен включать:

- ways с `highway=*` внутри requested cells;
- все nodes, referenced выбранными ways;
- исходные way/node IDs, versions, coordinates и tags;
- response metadata с OSM base timestamp.

Relations, включая `route=foot`/`route=hiking`, в profile v1 не загружаются. Их нельзя
добавлять условно в зависимости от размера ответа: это сделало бы semantics одного
dataset profile недетерминированной. Если relations понадобятся routing-у, они войдут в
отдельную явно версионированную revision profile.

Запрещено заранее отбрасывать ways по `access`, `foot`, `surface`, `tracktype` или
`sac_scale`: интерпретация tags относится к будущему routing profile WarpBuster.

Query template и dataset profile имеют стабильные версии. Изменение состава данных
создаёт новую cache namespace и не переинтерпретирует старые blobs молча.

## Overpass client

Manager использует read-only Overpass API через HTTP POST. Endpoint configurable; для
первого запуска поставляется документированный default, чтобы ручная настройка не была
обязательной.

Требования:

- явный `User-Agent` с названием и версией проекта;
- gzip/content compression;
- bounded connect/read/total timeout;
- bounded retry count с backoff и jitter;
- корректная обработка `429`, `5xx`, timeout и malformed response;
- отсутствие бесконечного retry;
- отсутствие автоматического fan-out по множеству публичных endpoints;
- запросы только для отсутствующего/refresh coverage;
- response size limit до полной публикации в cache;
- temporary download и atomic rename после validation;
- live Overpass не используется обязательными CI tests.

Overpass получает только географические query cells и dataset filter. GPX file,
название файла, timestamps, elevation, FIT data и telemetry не отправляются.

## Cache model

Cache располагается в platform-native user cache directory. Расположение можно
переопределить CLI-параметром и environment/config значением с явным приоритетом.
Cache никогда не создаётся внутри repository автоматически.

Логическая структура:

```text
osm-cache/
  blobs/
    <sha256>.osm.xml.gz
  imports/
    <sha256>.osm.pbf
  snapshots/
    <snapshot-id>/manifest.json
  indexes/
    coverage.sqlite
  locks/
```

Raw blobs и imported data immutable. Новый download/refresh создаёт новый content hash;
existing blob не перезаписывается.

Snapshot представляет immutable набор blobs/imports, совместно покрывающих request.
Snapshot ID детерминированно зависит как минимум от:

- dataset profile/version;
- упорядоченного набора content hashes;
- coverage cell scheme/version.

Одинаковый набор данных не создаёт разные logical snapshot IDs из-за времени запуска
или порядка сетевых ответов.

### Snapshot manifest

Manifest содержит:

- `protocol_version`;
- `manifest_version`;
- `manager_version`;
- `snapshot_id`;
- `dataset_profile`;
- `created_at`;
- `osm_base_timestamp` или явное `unknown`;
- request fingerprint без исходного имени GPX;
- coverage scheme/version и cell IDs;
- requested buffer и safety metrics;
- абсолютные paths к data files в CLI response;
- для каждого файла: media type, size и SHA-256;
- source kind (`overpass`/`import`);
- endpoint для Overpass blobs;
- freshness/stale state;
- OSM attribution и license URL.

Manifest не содержит GPX metadata, FIT data или telemetry.

## Freshness и offline semantics

Начальный default policy:

- fresh полное покрытие возвращается без network access;
- отсутствующее покрытие загружается синхронно до ответа `READY`;
- stale покрытие online обновляется, если не задана более мягкая policy;
- при network failure полное stale покрытие разрешено, если нет `--require-fresh`;
- partial stale coverage без полного snapshot считается ошибкой;
- `--offline` никогда не открывает network connection;
- `--refresh` создаёт новый immutable snapshot и не удаляет старый;
- текущий `ensure` возвращает один зафиксированный snapshot, который не меняется после
  возврата результата.

Слово «автоматически» означает программную загрузку внутри `ensure`, а не постоянный
background daemon. Команда может показывать progress, но успешный ответ возвращается
только после публикации полного snapshot.

## Configuration

Все bounds имеют имена, единицы, комментарии, defaults и tests. Начальные defaults:

| Поле | Default | Единицы | Назначение |
|---|---:|---|---|
| `gpx_corridor_buffer_m` | 1000 | m | Buffer вокруг GPX/line geometry |
| `cache_grid_zoom` | 12 | level | Стабильная geographic cell scheme v1 |
| `coverage_sample_cell_fraction` | 0.5 | cell fraction | Шаг sampling линии относительно размера cell |
| `default_max_age_seconds` | 2592000 | s | Freshness, 30 дней |
| `maximum_requested_area_km2` | 2000 | km² | Hard limit одного ensure |
| `maximum_ensure_cells` | 512 | cells | Bound разбиения/запросов |
| `maximum_cells_per_overpass_request` | 32 | cells | Максимум соседних cells в одном Overpass request |
| `maximum_overpass_requests` | 128 | requests | Максимальный fan-out одного ensure |
| `maximum_download_bytes` | 268435456 | bytes | 256 MiB на один response |
| `maximum_ensure_download_bytes` | 536870912 | bytes | 512 MiB за один ensure |
| `network_timeout_seconds` | 180 | s | Общий HTTP timeout |
| `maximum_retry_count` | 2 | retries | Дополнительные попытки |
| `retry_backoff_seconds` | 1 | s | Начальная задержка exponential backoff |
| `retry_jitter_seconds` | 0.25 | s | Максимальный случайный jitter retry |
| `cache_lock_timeout_seconds` | 60 | s | Ожидание concurrent writer |
| `stale_lock_seconds` | 900 | s | Минимальный возраст abandoned lock для recovery |
| `lock_poll_seconds` | 0.1 | s | Интервал ожидания cache lock |
| `http_read_chunk_bytes` | 65536 | bytes | Размер streaming read/hash chunk |
| `maximum_input_file_bytes` | 33554432 | bytes | Максимальный GPX/GeoJSON request file |
| `maximum_import_bytes` | 2147483648 | bytes | Максимальный локальный OSM import |
| `maximum_gpx_points` | 1000000 | points | Максимум GPX track/route points |
| `maximum_gpx_segment_length_m` | 100000 | m | Максимум одного geometry segment |
| `maximum_gpx_total_length_m` | 5000000 | m | Максимальная суммарная длина geometry |
| `maximum_osm_objects` | 5000000 | objects | Bound streaming OSM validation |
| `prune_minimum_age_seconds` | 86400 | s | Минимальный возраст удаляемого unreferenced blob |

`cache_grid_zoom` задаёт только разбиение raw OSM data cache и не означает загрузку
raster/vector tiles.

Config validation отклоняет non-positive, contradictory и unsafe combinations.
Overriding safety limits отражается в manifest и console/JSON diagnostics.
Полный конфигурационный контракт и понятные комментарии поставляются также в
`packages/osm-manager/osm-manager.example.toml`; приоритет: CLI → TOML → environment →
defaults.
Файл `./osm-manager.toml` загружается автоматически из текущей рабочей директории;
`--config` выбирает другой файл явно. Родительские директории не обходятся.

## CLI JSON protocol v1

При `--json` stdout содержит ровно один JSON document. Progress, retry и human-readable
diagnostics направляются в stderr.

Минимальный успешный ответ `ensure`:

```json
{
  "protocol_version": 1,
  "operation": "ensure",
  "status": "ready",
  "snapshot_id": "sha256:...",
  "manifest_path": "/absolute/path/to/manifest.json",
  "data_files": [
    {
      "path": "/absolute/path/to/blob.osm.xml.gz",
      "media_type": "application/vnd.openstreetmap.data+xml+gzip",
      "sha256": "...",
      "size_bytes": 12345
    }
  ],
  "downloaded": false,
  "stale": false,
  "warnings": []
}
```

Error response при `--json` также является валидным protocol document и содержит:

- `protocol_version`;
- `operation`;
- `status=error`;
- стабильный `error_code`;
- безопасное human-readable `message`;
- измеримые details без traceback и secrets.

Минимальные stable error codes:

- `INVALID_INPUT`;
- `INVALID_GPX`;
- `REQUEST_LIMIT_EXCEEDED`;
- `OFFLINE_CACHE_MISS`;
- `FRESH_CACHE_REQUIRED`;
- `OVERPASS_UNAVAILABLE`;
- `RESPONSE_LIMIT_EXCEEDED`;
- `OSM_DATA_INVALID`;
- `CACHE_IO_ERROR`;
- `CACHE_LOCK_TIMEOUT`;
- `PROTOCOL_UNSUPPORTED`.

Exit codes:

- `0` — snapshot ready, включая разрешённый stale fallback;
- `2` — invalid input/config/protocol;
- `3` — data/network unavailable;
- `4` — invalid/oversized OSM response;
- `5` — cache/lock/filesystem failure.

`capabilities --json` позволяет будущему WarpBuster проверить protocol version до
вызова `ensure`.

## Concurrency и atomicity

- Два `ensure` для одинаковых отсутствующих cells не должны выполнять дублирующие
  downloads после получения lock.
- Locks имеют bounded timeout и metadata владельца/process start.
- Stale lock можно удалить только после проверяемого expiration policy.
- Download, validation, hashing и manifest construction выполняются до atomic publish.
- Crash не оставляет entry, который последующий `ensure` считает valid.
- Readers никогда не видят частично записанный manifest/blob.
- Index можно полностью перестроить из immutable blobs и manifests.

## Privacy и безопасность

- Manager не читает FIT.
- GPX geometry обрабатывается локально.
- В сеть уходят только bounded geographic cells и OSM tag query.
- Console явно сообщает, когда выполняется network request и какой endpoint получает
  географическую область.
- Cache считается location-sensitive local data.
- Paths и GPX filenames не передаются Overpass.
- URL credentials/query secrets не поддерживаются в command line и не печатаются.
- Redirects на неожиданные schemes/hosts не принимаются молча.
- TLS verification включена по умолчанию и не отключается hidden flag-ом.
- XML parser не разрешает внешние entities/resources.

## OSM license и attribution

Каждый manifest содержит:

- `OpenStreetMap contributors`;
- `https://www.openstreetmap.org/copyright`;
- ссылку на ODbL.

CLI `inspect` показывает attribution. Будущий WarpBuster report сможет перенести её из
manifest. Task 009 не реализует UI или карту.

Cache raw OSM data не является cache стандартных `tile.openstreetmap.org`; raster tile
usage и offline prefetch не входят в Manager.

## Integration contract для будущего WarpBuster

Task 009 реализует и тестирует contract, но не меняет CLI WarpBuster.

Будущий caller должен иметь возможность:

1. Найти executable `warpbuster-osm`.
2. Проверить `capabilities --json`.
3. Передать GPX напрямую через `ensure --gpx`, либо сформировать protocol request.
4. Получить абсолютный `manifest_path` и immutable `data_files`.
5. Проверить hashes.
6. Использовать snapshot read-only.
7. Сохранить `snapshot_id` и provenance в reconstruction report.

Manager не запускает callback, не импортирует WarpBuster и не изменяет repository.

## Тесты

### GPX и coverage

- GPX 1.0/1.1 с namespaces;
- `trk`, multiple `trkseg`, `rte` и их union;
- игнорирование elevation/timestamps/extensions/wpt;
- malformed XML;
- invalid/missing coordinates;
- outlier, превышающий area/cell limits;
- antimeridian course;
- corridor cells вместо полного bbox длинного/кольцевого GPX;
- deterministic request fingerprint.

### Fetch и cache

- first ensure выполняет download;
- second identical ensure выполняет zero network requests;
- overlapping request скачивает только missing cells;
- refresh создаёт новый immutable snapshot;
- offline complete hit;
- offline miss;
- stale fallback;
- require-fresh refusal;
- timeout, `429`, `5xx`, malformed и oversized responses;
- bounded retry;
- reference-complete OSM data validation;
- atomic crash recovery;
- concurrent ensure и lock timeout;
- shared blob reference preservation при remove/prune;
- index rebuild из manifests/blobs.

HTTP tests используют local fake server/transport. Public Overpass не является частью
обычного CI. Опциональный marked smoke test разрешён только вручную.

### Protocol и packaging

- stdout содержит только JSON в `--json` mode;
- progress находится в stderr;
- stable status/error codes и exit codes;
- `capabilities` version negotiation;
- install wheel в чистый temporary environment;
- CLI работает независимо от установленного WarpBuster Core;
- Ruff, format, mypy и pytest зелёные.

## Performance и resource bounds

- Coverage lookup использует индекс, а не полный scan всех cache entries.
- Cell/blob deduplication не является O(n²).
- OSM parsing/validation выполняется streaming или другим bounded-memory способом.
- Response size проверяется во время чтения, а не после полной загрузки в RAM.
- На synthetic fixture со 100 000 OSM objects validation, hashing и publication должны
  завершаться менее чем за 5 секунд на современном ноутбуке, без учёта network time.
- Performance test имеет отдельный marker, но алгоритмический bounded regression входит
  в обычный suite.

## Acceptance Criteria

- подпроект отдельно устанавливается и предоставляет `warpbuster-osm`;
- `ensure --gpx race.gpx` без ручного OSM download создаёт полный immutable snapshot;
- GPX сам определяет corridor/cells и не отправляется на сервер;
- повторный `ensure` для той же области не обращается к сети;
- частично покрытая область загружает только недостающие cells;
- offline hit работает, offline miss имеет stable machine-readable отказ;
- stale/refresh/require-fresh semantics соответствуют спецификации;
- snapshot детерминирован, content-addressed и полностью аудируем;
- повреждённый/частичный download не публикуется;
- concurrent ensure не портит cache и не дублирует committed blobs;
- area, cell, response, timeout и retry bounds находятся в config и покрыты tests;
- Manager не читает FIT и не содержит routing/reconstruction logic;
- JSON protocol достаточен для будущего subprocess-вызова из WarpBuster;
- OSM attribution/license присутствуют в manifest и inspect output;
- обязательные tests не используют live network;
- полный test/lint/format/type-check и clean-wheel-install suite зелёный.

## Сознательно не реализуется

- изменение WarpBuster CLI;
- автоматический вызов Manager из WarpBuster;
- OSM graph и pedestrian access interpretation;
- routing, A*/Dijkstra/k-shortest paths;
- GPX-to-OSM map matching;
- FIT reconstruction;
- start/finish/via constraints;
- DEM;
- raster/vector tile download или hosting;
- daemon/background service;
- web UI;
- cloud cache/account sync;
- автоматическая публикация OSM-derived datasets.

## Официальные ссылки

- Overpass query language:
  `https://wiki.openstreetmap.org/wiki/Overpass_API/Language_Guide`
- OSM data downloading guidance:
  `https://wiki.openstreetmap.org/wiki/Getting_Data`
- OSM API v0.6 scope:
  `https://wiki.openstreetmap.org/wiki/API_0.6`
- OSM copyright/ODbL/attribution:
  `https://www.openstreetmap.org/copyright`
- OSMF raster tile policy:
  `https://operations.osmfoundation.org/policies/tiles/`
- pyosmium documentation:
  `https://docs.osmcode.org/pyosmium/latest/`
