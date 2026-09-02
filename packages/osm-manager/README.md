# WarpBuster OSM Manager

`warpbuster-osm` downloads, validates, versions, and caches immutable raw
OpenStreetMap snapshots for later use by WarpBuster.

It does not read FIT, build routes, perform map matching, or reconstruct activity
coordinates.

```bash
warpbuster-osm ensure --gpx race.gpx
warpbuster-osm ensure --gpx race.gpx --offline --json
warpbuster-osm ensure --geojson corridor.geojson
warpbuster-osm ensure --bbox 33.5,44.3,33.8,44.6
warpbuster-osm refresh --gpx race.gpx
warpbuster-osm import region.osm.pbf
warpbuster-osm list
warpbuster-osm inspect sha256:...
warpbuster-osm prune --dry-run
warpbuster-osm doctor
```

All network/cache limits and defaults live in
`warpbuster_osm_manager/config.py`. При запуске Manager автоматически ищет
`osm-manager.toml` только в текущей рабочей директории. Другой файл можно выбрать через
`--config`; если default-файла нет, используются environment variables и встроенные
defaults.

Полный комментированный шаблон со всеми доступными параметрами находится в
`osm-manager.example.toml`. Приоритет настроек: CLI override → явно выбранный или
автоматический TOML → environment → defaults. Environment variables поддерживаются для
`WARPBUSTER_OSM_CACHE_DIR` и `WARPBUSTER_OSM_OVERPASS_URL`; остальные параметры задаются
в TOML.

```bash
cp packages/osm-manager/osm-manager.example.toml osm-manager.toml
warpbuster-osm ensure --gpx race.gpx
```

OpenStreetMap data is available under the Open Database License. See
<https://www.openstreetmap.org/copyright>.

При импорте `.osm`, `.osm.gz` или `.osm.pbf` файл обязан содержать объявленные bounds.
В coverage index попадают только Web Mercator cells, целиком закрытые этими bounds;
пограничные неполные cells не создают ложных offline cache hits.

Поле `stale` в ответе `ensure` отражает freshness на момент вызова. Immutable manifest
хранит состояние на момент своего создания и per-cell `fetched_at`, поэтому его content
и `snapshot_id` не меняются с течением времени.
