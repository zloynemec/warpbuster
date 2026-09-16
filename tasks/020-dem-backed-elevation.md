# Task 020 — DEM-backed Elevation

Статус: Task 020 (020A–020D) выполнена 2026-09-16. ТЗ уточнено по
результатам feasibility-проверки 2026-09-15.

## Простыми словами

WarpBuster уже умеет получить один или несколько правдоподобных 2D-маршрутов из OSM.
Task 020 добавляет независимую локальную модель рельефа: для заданной геометрии можно
заранее получить необходимые DEM-тайлы, затем без сети воспроизводимо вычислить профиль
высоты, выгрузить маршрут в GPX с `<ele>` и сформировать диагностическое сравнение с
имеющейся высотой FIT/course.

DEM является только enrichment/evidence на стадии Reconstruction. Он не участвует в
Integrity Detector, не доказывает corruption и не разрешает менять правдоподобные
координаты или altitude.

## Результат feasibility-проверки

Для pinned `pyvalhalla 3.8.3` практически подтверждено:

- wheel содержит `valhalla.Actor.height()` и `valhalla_build_elevation`;
- Skadi читает локальные `*.hgt.gz` без OSM graph и без network;
- формат тайла — 1° × 1°, 3601 × 3601 signed big-endian int16 samples;
- полный распакованный размер одного тайла — 25 934 402 bytes;
- sampling использует bilinear interpolation и исключает void/no-data samples;
- при отсутствии тайла API возвращает `null`, а не выдуманную высоту;
- повторное чтение локального тайла не требует network;
- проверенный `N44E033.hgt.gz` имел размер 4 456 361 bytes, SHA-256
  `7bd0df0cf2c82026efd7a7ece4a4a74c9619a62d91b79c5b25a310ddeb8c64f2` и давал
  конечные значения 7, 45 и 144 m для трёх тестовых точек на суше;
- морские точки могут иметь отрицательную bathymetry из ETOPO1 — это допустимые данные,
  а не автоматически invalid elevation.

Штатный `valhalla_build_elevation` **не принимается как production cache manager**:

- существующий файл пропускается без проверки gzip, размера и hash;
- gzip-download пишется непосредственно в целевой path;
- встроенные retries способны занять до 24 минут;
- endpoint не предоставляет WarpBuster immutable snapshot manifest;
- у проверенного объекта AWS отсутствовал version ID.

Утилиту допускается использовать в документации и ручных probes, но runtime Task 020
должен иметь собственные acquisition, validation, locking и atomic publication.

## Выбранный dataset profile

Первая production-реализация поддерживает ровно один явно версионированный профиль:

```text
profile_id: mapzen-skadi-egm96-v1
provider: Mapzen/Tilezen Terrain Tiles on AWS Open Data
format: Skadi / SRTMGL1-compatible HGT gzip
horizontal_crs: EPSG:4326
vertical_datum: WGS84/EGM96 geoid
units: metres
tile_extent: 1 degree × 1 degree
stored_grid: 3601 × 3601
nominal_spacing: 1 arc-second
void_value: -32768
```

020A реализована как immutable `SkadiDatasetProfile` с canonical JSON/SHA-256 и
командой `warpbuster-osm-route dem profile --json`. Профиль фиксирует формат,
WGS84/EGM96, номинальный шаг сетки и отдельный версионированный attribution bundle.
Фактическая точность не объявляется равной шагу сетки: upstream resolution зависит от
региона и доступности source data. 020A не загружает и не семплирует тайлы.

Источник download:

```text
https://elevation-tiles-prod.s3.us-east-1.amazonaws.com/
  skadi/{LAT_DIR}/{TILE_NAME}.hgt.gz
```

Это составной и преимущественно bare-earth dataset, а не данные OpenStreetMap. В разных
регионах фактическими источниками могут быть SRTM, EU-DEM, GMTED, ETOPO1, 3DEP и другие
наборы с разным реальным resolution. Размер сетки 3601 × 3601 не означает, что исходная
точность везде равна 1 arc-second. Task 020 не должен обозначать профиль как «OSM
elevation» и не должен обещать точность конкретного upstream source.

Набор включает bathymetry. Отрицательная высота допустима; значение может считаться
неподходящим для наземного маршрута только при наличии отдельного route/land evidence,
которое не входит в Task 020.

Полезные первичные документы:

- [AWS Terrain Tiles](https://aws.amazon.com/marketplace/pp/prodview-x7vtai3hasf26);
- [Tilezen: formats](https://github.com/tilezen/joerd/blob/master/docs/formats.md);
- [Tilezen: data sources](https://github.com/tilezen/joerd/blob/master/docs/data-sources.md);
- [Tilezen: attribution](https://github.com/tilezen/joerd/blob/master/docs/attribution.md);
- [Valhalla Skadi sampler](https://github.com/valhalla/valhalla/blob/master/src/skadi/sample.cc);
- [Valhalla elevation downloader](https://github.com/valhalla/valhalla/blob/master/scripts/valhalla_build_elevation).

## Цель

Реализовать optional deterministic DEM subsystem, которая:

1. вычисляет bounded список Skadi-тайлов для заданной геометрии и buffer;
2. получает отсутствующие тайлы только в `AUTO` mode;
3. полностью проверяет каждый download до публикации;
4. создаёт immutable content-addressed DEM snapshot;
5. повторно использует snapshot без сети в `OFFLINE` mode;
6. детерминированно семплирует высоту вдоль route polyline;
7. явно сообщает missing coverage, voids и datum incompatibility;
8. формирует raw и filtered profiles, ascent/descent и GPX 1.1 с `<ele>`;
9. предоставляет только диагностическое сравнение с уже сопоставленными FIT/course
   altitude observations;
10. сохраняет dataset, tile, engine, policy и artifact provenance с раздельными
    идентификаторами исходного DEM snapshot и вычисленного elevation profile.

## Архитектурная граница

DEM размещается рядом с Valhalla adapter в distribution `warpbuster-osm-routing`, но не
становится частью OSM Manager или OSM graph identity:

```text
OSM Manager snapshot ──→ GraphCache ──→ graph_id ──→ 2D route geometry

Mapzen/Tilezen Skadi ──→ DemCache   ──→ dem_snapshot_id
                                             │
2D route geometry ───────────────────────────┴──→ ElevationService
                                                    │
                                                    ├─ raw profile
                                                    ├─ filtered profile
                                                    ├─ ascent/descent
                                                    ├─ GPX + <ele>
                                                    └─ evidence/audit JSON
```

Обязательные свойства границы:

- `graph_id` и `dem_snapshot_id` независимы;
- изменение DEM не требует rebuild Valhalla graph;
- изменение OSM snapshot не переинтерпретирует существующий DEM snapshot;
- sampler принимает обычную WGS84 polyline и не вызывает routing;
- Core не импортируется в низкоуровневые cache/acquisition modules;
- OSM Manager не загружает и не хранит DEM;
- `Actor.height()` скрыт за узким typed adapter и может быть заменён;
- runtime config Valhalla получает explicit DEM path, а не неявный default
  `/data/valhalla/elevation/`;
- network разрешён только acquisition layer; sampling всегда offline.

В production рекомендуемый отдельный root — `/data/cache/dem`. Он конфигурируется и не
зашивается в библиотеку. Полная административная очистка application cache должна
включать этот root, но очистка OSM graph не обязана удалять DEM и наоборот.

## Режимы работы

Поддерживаются те же смысловые режимы, что у общего pipeline:

- `AUTO`: проверить local cache, boundedly скачать недостающее, опубликовать snapshot;
- `OFFLINE`: запретить DNS/HTTP и использовать только полностью проверенные local tiles;
- `DISABLED`: не готовить DEM и вернуть явный статус `DISABLED`.

`AUTO` не делает результат обязательным: network timeout, 404 или неполное покрытие дают
структурированную диагностику и не отменяют уже допустимый 2D route. Cache corruption,
однако, не маскируется как cache miss: повреждённый опубликованный artifact даёт
`DEM_CACHE_CORRUPT` и никогда не используется.

## Coverage planning

Входом planner является непустая конечная WGS84 polyline и именованный buffer в метрах.
Planner обязан:

- проверить coordinate count, finite values и диапазоны latitude/longitude;
- построить bounded buffered coverage без наивного полного перебора мировой сетки;
- корректно обрабатывать отрицательные координаты, equator, prime meridian и границу
  соседних 1° tiles;
- либо корректно поддержать antimeridian, либо вернуть отдельный documented error;
- отсортировать tile names канонически;
- дедуплицировать tiles;
- до network проверить maximum tile count и ожидаемый storage budget.

Buffer, maximum points, maximum tiles, maximum compressed/uncompressed bytes и deadline
являются именованными полями typed config с единицами, defaults, validation и тестами.
Никаких скрытых magic numbers.

## Immutable DEM cache

020B реализована в `warpbuster_osm_routing.dem_coverage` и `.dem_cache` с отдельным
root, полным tile/snapshot validation, `AUTO/OFFLINE/DISABLED`, content-addressed
objects, атомарной публикацией, typed errors, object quota и dry-run/apply prune.
Snapshot создаётся только при полном coverage; при отказе optional DEM не блокирует
2D route. `lease` защищает snapshot и его объекты от prune для будущего sampler.
На данный момент глобальный maintenance lock сериализует acquisition/prune; это
консервативное ограничение, а не обещание параллельных загрузок разных тайлов.

Рекомендуемая логическая структура:

```text
dem-cache/
  objects/
    sha256/<prefix>/<tile-sha256>.hgt.gz
  snapshots/
    <dem-snapshot-id-without-prefix>/
      manifest.json
  index/
    mapzen-skadi-egm96-v1/
      N44/N44E033.json
  staging/
  locks/
```

`objects` immutable и content-addressed. Snapshot manifest ссылается только на
проверенные object hashes; mutable index используется лишь для поиска последнего
известного объекта по tile name и не является доказательством целостности.

### Download и публикация

Каждый tile:

1. загружается в уникальный staging file с exclusive create;
2. ограничивается connect/read/total deadline и maximum compressed bytes;
3. проверяется по HTTP status и отсутствию redirect на недоверенный origin;
4. полностью проходит gzip CRC/decompression;
5. обязан распаковаться ровно в 25 934 402 bytes;
6. проверяется как 3601 × 3601 big-endian int16 raster;
7. получает SHA-256 compressed artifact;
8. fsync/atomic rename публикует object;
9. только после публикации всех objects атомарно публикуется snapshot manifest.

Partial files не должны выглядеть как cache hit. При failure staging удаляется либо
явно помечается как непригодный и затем boundedly очищается. Concurrent процессы для
одного tile/snapshot координируются lock; stale lock определяется именованной policy.
Ожидание lock входит в общий deadline.

HTTP `ETag`, `Last-Modified`, final URL и observed size сохраняются как provenance, но
не заменяют SHA-256 и не считаются immutable version ID.

### `dem_snapshot_id`

Имеет вид `sha256:<64 lowercase hex>` и вычисляется из canonical JSON:

- cache-key schema version;
- dataset profile ID;
- horizontal CRS, vertical datum и units;
- канонически отсортированные tile names и SHA-256;
- artifact format и validation schema version.

В identity не входят absolute paths, PID, timestamps, cache location, URL query order,
logging verbosity и порядок входных points.

Manifest дополнительно содержит acquisition timestamps и HTTP metadata, coverage,
sizes, attribution version и полный artifact inventory. `inspect` повторно проверяет
manifest schema, paths, sizes, hashes, gzip и raster dimensions в пределах budget.
Версии Valhalla, sampling и filtering не входят в data snapshot identity: смена движка
не должна дублировать неизменившиеся HGT objects.

## Elevation sampling

Production backend использует pinned `valhalla.Actor.height()` через typed
`ElevationBackend`. Прямой raw JSON Valhalla не выходит за adapter.

Sampling contract:

- входная polyline не изменяется;
- sampling ведётся по geodesic chainage в метрах;
- исходные vertices всегда сохраняются;
- длинные segments детерминированно densify по именованному maximum spacing;
- total samples проверяется до исполнения и имеет hard limit;
- одинаковые geometry, snapshot, engine и policy дают одинаковые sample chainages и
  heights;
- raw finite elevation сохраняется без округления внутри модели;
- presentation rounding не участвует в расчётах;
- отсутствующий tile, void и engine error различаются;
- `null` не заменяется нулём, соседней точкой или FIT/course altitude;
- partial coverage возвращается как `PARTIAL`, а не `READY`;
- bilinear interpolation Valhalla фиксируется как часть sampling semantics version;
- значения ниже уровня моря допустимы и не фильтруются только по знаку.

Для range sampling нельзя полагаться на неограниченный engine response. Request и
response ограничиваются point count и bytes; malformed, non-finite или лишние значения
дают typed error.

`elevation_profile_id` имеет вид `sha256:<64 lowercase hex>` и вычисляется отдельно из
`dem_snapshot_id`, canonical geometry hash, Valhalla version, sampling policy hash и
filtering policy hash. Изменение движка или вычислительной policy меняет profile ID, но
не data snapshot ID.

## Raw и filtered elevation profiles

Результат содержит две разные серии:

- `raw`: значения DEM backend на canonical sample chainages;
- `filtered`: производная серия только для presentation и ascent/descent.

Filtering policy имеет стабильный ID, canonical inspection document и hash. Все
параметры задаются в метрах/метрах пути, находятся в config model, документированы и
покрыты тестами. До реализации необходимо зафиксировать один детерминированный
distance-domain алгоритм, который:

- не соединяет участки через missing samples;
- не использует timestamps, pace, FIT/course altitude или конкретную тренировку;
- не меняет route geometry;
- не создаёт значения вне конечного локального диапазона raw samples;
- одинаково обрабатывает начало/конец и короткие segments;
- считает ascent/descent отдельно внутри каждого непрерывного covered segment;
- публикует raw и filtered totals раздельно, не выдавая их за device ascent.

Конкретные defaults smoothing/hysteresis должны быть обоснованы synthetic fixtures и
реальными probes до завершения Task 020. Они не могут появиться в коде как безымянные
константы.

020D фиксирует `distance-triangle-excursion-v1`: треугольное расстояние-взвешенное
среднее в радиусе 60 м внутри каждого covered segment, с сохранением его endpoints;
для filtered ascent/descent используется peak/valley hysteresis с разворотом от 3 м.
Raw totals считаются без порога. Synthetic fixtures проверяют flat, 1 м sawtooth, постепенный
4 м climb, short segment, gap и отрицательные высоты. Повторный публичный probe
`N44E033` для 457 samples между ранее проверенными точками 7 и 144 м дал raw
ascent/descent 571/434 м и filtered 526.1/388.9 м; net rise практически сохранён.
Это консервативная presentation policy, не калибровка точности DEM и не device ascent.
Policy ID/hash меняются при смене параметров; raw не меняется.

## GPX export

Task 020 экспортирует отдельный route GPX 1.1, но не переписывает исходный FIT.

- latitude/longitude берутся без изменений из canonical sampled route;
- `<ele>` содержит filtered DEM elevation в метрах;
- для missing elevation `<ele>` отсутствует, а не равен `0`;
- точки с missing elevation сохраняются;
- timestamps не создаются и не изменяются;
- metadata/sidecar audit связывают GPX с `graph_id` при его наличии,
  `dem_snapshot_id`, dataset profile и filtering policy hash;
- XML output детерминирован для одинакового typed result;
- имя/description не содержат абсолютных paths или исходных имён приватных файлов;
- raw profile и diagnostics сохраняются в JSON audit, а не подменяют `<ele>`.

## Сравнение с FIT/course altitude

Task 020 не выполняет map matching или новое выравнивание временных рядов. Comparison
API принимает только observations, которые caller уже явно сопоставил с route chainage,
и сохраняет происхождение каждого значения.

Для каждого источника фиксируются:

- source kind (`FIT`, `GPX_COURSE`, `DEM`);
- altitude field/происхождение, если оно достоверно известно;
- units;
- declared vertical datum либо `UNKNOWN`;
- coverage и missing count;
- signed/absolute residual statistics только для допустимых пар.

Нельзя угадывать FIT semantics или vertical datum. Если datum FIT/course неизвестен либо
несовместим с EGM96, результат получает `DATUM_UNKNOWN`/`DATUM_MISMATCH`; абсолютный
offset не используется для confidence или repair. Допускаются shape-relative метрики
после удаления явно описанного robust offset, но они остаются diagnostic evidence.

В Task 020 comparison ничего не выбирает, не ранжирует и не применяет автоматически.

## Typed API и CLI

Ожидаемый API package `warpbuster_osm_routing`:

```text
DemCoveragePlanner.plan(polyline, policy) -> DemCoveragePlan
DemCache.ensure(plan, mode)                -> DemSnapshot
DemCache.inspect(dem_snapshot_id)          -> DemSnapshot
ElevationService.profile(request)          -> ElevationProfileResult
ElevationGpxWriter.write(result, path)      -> ElevationGpxArtifact
```

CLI расширяет существующий `warpbuster-osm-route` отдельной группой `dem`, например:

```bash
warpbuster-osm-route dem prepare route.json --mode auto --json
warpbuster-osm-route dem inspect sha256:... --json
warpbuster-osm-route dem profile route.json --snapshot sha256:... --output route.gpx --json
warpbuster-osm-route dem prune --dry-run --json
```

Названия могут быть уточнены при реализации, но обязательны machine-readable JSON,
отдельный snapshot ID, explicit mode/cache config и отсутствие raw Valhalla requests.
Общий `warpbuster process` и Web не начинают использовать DEM автоматически в Task 020.

## Статусы и ошибки

Минимальный стабильный набор:

```text
READY
CACHED
PARTIAL
DISABLED

INVALID_REQUEST
RESOURCE_LIMIT_EXCEEDED
DEM_NETWORK_TIMEOUT
DEM_DOWNLOAD_FAILED
DEM_TILE_MISSING
DEM_TILE_CORRUPT
DEM_CACHE_CORRUPT
DEM_SNAPSHOT_NOT_FOUND
DEM_ENGINE_MISMATCH
DEM_RESPONSE_INVALID
DEM_ENGINE_ERROR
DEM_EXPORT_FAILED
OUTPUT_EXISTS
DATUM_UNKNOWN
DATUM_MISMATCH
```

Ожидаемые отсутствие coverage и unknown datum не маскируются generic exception.
Сообщения не содержат response body, cookies, private FIT/GPX contents или абсолютные
paths. Internal exception chaining сохраняется для debug logs.

## Attribution и provenance

MIT-лицензия Joerd относится к программе и не является единой лицензией составного DEM.
Task 020 обязан хранить и показывать актуальный attribution bundle Mapzen/Tilezen и
upstream providers. Минимально он покрывает SRTM/GMTED/3DEP, ETOPO1, EU-DEM и остальные
источники из официального attribution document.

Каждый JSON audit и GPX sidecar содержит:

- provider/dataset profile;
- source endpoint origin;
- horizontal CRS, vertical datum, units;
- tile names, hashes и byte sizes;
- `dem_snapshot_id`;
- Valhalla version и sampling semantics version;
- raw/filtered policy IDs и hashes;
- coverage/missing diagnostics;
- required attribution bundle/version.

Публичное отображение DEM-профиля в будущем Web обязано показывать attribution, но сама
Web-интеграция относится к следующей задаче.

## Privacy и эксплуатационные ограничения

Tile acquisition раскрывает AWS только координаты 1° tile, а значит приблизительный
регион маршрута. В Task 020 network вызывается только явно выбранным `AUTO`; `OFFLINE`
гарантированно не создаёт HTTP/DNS попыток.

Логи могут содержать tile names, snapshot ID, counts, sizes, timings и status, но не
полную route geometry, FIT records, device metadata или owner credentials. URL логируется
без чувствительных query parameters.

Полный мировой Skadi cache не является целью. Upstream script оценивает полный LZ4
набор примерно в 216 GB; Task 020 работает только с bounded coverage текущих маршрутов,
имеет quota/prune и никогда не скачивает мир по умолчанию.

## Ожидаемые изменения

Ориентировочно:

```text
packages/osm-routing/
  README.md
  osm-routing.example.toml
  src/warpbuster_osm_routing/
    cli.py
    config.py
    dem_models.py
    dem_coverage.py
    dem_cache.py
    dem_manifest.py
    elevation_backend.py
    elevation_service.py
    elevation_filter.py
    elevation_gpx.py
    errors.py
  tests/
    test_dem_coverage.py
    test_dem_cache.py
    test_dem_manifest.py
    test_elevation_backend.py
    test_elevation_service.py
    test_elevation_filter.py
    test_elevation_gpx.py
```

Core допускается затронуть только typed boundary для GPX/audit export, если companion
package не может владеть им без обратной зависимости. Integrity Detector,
coordinate mask, reconstruction selection/application, FIT writer и Web не меняются.

## Обязательные тесты

### Dataset/format

- tile naming во всех полушариях и на integer boundaries;
- exact raster dimensions и big-endian decode;
- gzip CRC, truncated gzip, extra bytes и decompression bomb limit;
- void `-32768`, partial-void bilinear interpolation и all-void result;
- допустимые zero и negative elevations;
- missing tile даёт `null`/`PARTIAL`, не fabricated value.

### Cache/acquisition

- first download → validated atomic publication;
- повторный `AUTO` и `OFFLINE` используют тот же hash без network;
- corrupt published object не трактуется как cache miss;
- interrupted download не становится object/index/snapshot;
- wrong content length, oversized response, timeout, 404 и untrusted redirect;
- concurrent ensure одного tile выполняет не более одного download;
- stale lock, deadline и quota;
- deterministic snapshot ID независимо от input point order и absolute cache path;
- изменение tile hash, dataset profile или validation schema меняет snapshot ID;
- изменение Valhalla/sampling/filtering меняет profile ID, но не snapshot ID;
- inspect обнаруживает manifest/path/size/hash/gzip corruption;
- prune не удаляет leased/in-use snapshot.

### Sampling/profile

- vertex preservation и deterministic distance-domain densification;
- tile boundary и multi-tile route;
- bilinear result на synthetic known raster;
- covered, partial и missing profiles;
- hard point/response-byte limits;
- повторный offline profile byte/semantic equality;
- raw values не меняются filtering/presentation layer;
- filtering не пересекает missing gaps;
- ascent/descent fixtures: flat, monotonic, sawtooth noise, short segment, gap;
- 20 000 bounded samples обрабатываются менее чем за 5 секунд на современном ноутбуке
  либо benchmark документирует измеренное отклонение до приёмки.

### GPX/comparison/safety

- deterministic valid GPX 1.1 с `<ele>` и без fabricated timestamps;
- missing `<ele>` не удаляет point и не превращается в zero;
- provenance/attribution sidecar;
- known compatible datum, `UNKNOWN` и mismatch cases;
- comparison не запускает detector/reconstruction и ничего не применяет;
- boundary test запрещает imports DEM в Integrity Detector;
- существующие Core, OSM Manager и OSM Routing tests остаются зелёными.

Большой реальный DEM-файл не коммитится. Public tests генерируют synthetic compressible
HGT во временной директории. Network integration test с реальным AWS tile маркируется
отдельно и не является обязательным для обычного offline CI.

## Acceptance criteria

- [x] Зафиксирован `mapzen-skadi-egm96-v1`, его формат, EGM96 datum, ограничения и
      полный attribution contract.
- [x] `DemCache` отделён от OSM Manager/GraphCache и имеет собственный cache root,
      manifest и `dem_snapshot_id`.
- [x] `AUTO`, `OFFLINE`, `DISABLED` имеют проверяемую семантику; sampling не выполняет
      network.
- [x] Download bounded, проверяется полностью и публикуется атомарно; partial/corrupt
      файл никогда не становится cache hit.
- [x] Повторный offline запуск даёт тот же snapshot/profile без HTTP/DNS.
- [x] Coverage planner корректно и boundedly вычисляет необходимые 1° tiles.
- [x] Typed backend использует pinned `Actor.height()` и проверяет response.
- [x] Raw/filtered profiles, missing diagnostics и ascent/descent детерминированы и
      связаны с именованной policy.
- [x] Route GPX 1.1 содержит DEM `<ele>`, не меняет geometry/timestamps и имеет audit
      sidecar с provenance/attribution.
- [x] FIT/course comparison различает unknown/incompatible datum и остаётся только
      diagnostic evidence.
- [x] DEM не импортируется Integrity Detector, не меняет corruption mask и не
      перезаписывает FIT altitude.
- [x] Нет автоматического ranking/application OSM alternatives — это Task 021.
- [x] Unit, integration, concurrency, corruption, offline, privacy и boundary tests
      проходят вместе со всеми предыдущими tests.
- [x] README/runbook документируют cache location, disk budgets, attribution, network
      disclosure, очистку и offline reuse.

## Не входит в Task 020

- использование DEM в Integrity Detector;
- доказательство GNSS corruption по elevation mismatch;
- выбор или ранжирование OSM route alternatives;
- автоматическое применение 2D route на основании высоты;
- изменение, заполнение или сглаживание altitude исходного FIT;
- изменение timestamps, speed или recorded distance;
- собственный DEM interpolation engine вместо принятого Valhalla backend;
- мировой prefetch;
- новые DEM providers или автоматическое смешивание datasets;
- Web UI и автоматическое включение DEM в production processing;
- hillshade, contours, 3D-карта и vendor APIs.

Эти ограничения сохраняют разделение Detection/Reconstruction и главный инвариант:
правдоподобное движение и правдоподобные FIT-поля не меняются без независимого
доказательства corruption.

## Команды проверки после реализации

Минимальный набор для отчёта Task 020:

```bash
cd packages/osm-routing
../../.venv/bin/python -m pytest -q
../../.venv/bin/python -m ruff check .
../../.venv/bin/python -m ruff format --check .
../../.venv/bin/python -m mypy src

cd ../..
.venv/bin/python -m pytest tests -q
git diff --check
```

В финальном отчёте отдельно показать первый `AUTO` download, повторный network-denied
`OFFLINE` run, одинаковые snapshot/profile hashes, corruption test, measured resource
usage и подтверждение, что FIT/detector/reconstruction не изменялись.
