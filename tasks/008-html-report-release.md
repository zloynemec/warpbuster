# Task 008 — HTML Report + v0.1 Stabilization

Статус: завершена.

## Цель

Сделать v0.1 удобной для диагностики и ручного тестирования.

## Сделать

Добавить local interactive reports:

```bash
warpbuster analyze activity.fit --html report.html
warpbuster analyze activity.gpx --html report.html
warpbuster repair activity.fit --course course.gpx --dry-run --html report.html
warpbuster repair activity.fit --course course.gpx --output fixed.fit --html report.html
```

`--json` и `--html` совместимы: JSON/console остаётся в stdout, HTML записывается в
указанный файл. Existing HTML не перезаписывается молча; `repair --overwrite` разрешает
явную атомарную замену.

Минимальный HTML:
- summary;
- integrity status/confidence;
- map;
- original vs repaired track;
- optional course;
- anomalies/интервалы;
- speed graph;
- altitude;
- HR при наличии;
- reasons;
- FIT diff summary.
- original/course/repaired distance и elevation comparison;
- missing-position runs table с anchors, временем, chord и distance delta.

Отчёт должен:

- открываться напрямую с локального диска без HTTP server;
- использовать Leaflet 1.9.4 с pinned CDN URL и стандартные OpenStreetMap tiles;
- показывать обязательную OpenStreetMap attribution;
- поддерживать pan, wheel/button zoom, scale, fit-to-track и layer switching;
- показывать start/end и markers через каждый 1 km recorded distance;
- хранить application CSS, JavaScript и report data внутри одного HTML;
- разрывать track polyline на continuity boundaries и missing coordinates;
- показывать missing-position connections отдельным dashed bridge layer, никогда не
  соединяя continuity boundaries;
- показывать original/repaired/course отдельными слоями;
- отличать corrupted, geometry-warning, applied и skipped regions;
- использовать elapsed time как основную ось графиков, а при отсутствии timestamps —
  record index;
- экранировать source metadata и безопасно встраивать JSON;
- детерминированно строиться из одинаковых inputs.
- не смешивать embedded FIT distance, coordinate geometry и GPX elevation semantics.

Первоначальный локальный Canvas был заменён на Leaflet/OSM: без географической подложки
отчёт оказался недостаточен для ручного анализа реальных GNSS ошибок. OSM используется
только renderer-ом и не участвует в detection, matching или reconstruction.

## Финальная стабилизация

- CLI help;
- README;
- example workflow;
- packaging;
- performance check;
- clean install check;
- full regression suite.

Дополнительно:

- проверить wheel и clean install в отдельном temporary venv;
- проверить включение HTML assets в package;
- измерить analyze и HTML generation на private активности около 20 000 records;
- синхронизировать устаревшие CLI/docs contracts с partial repair policy;
- после выполнения DoD сменить `0.1.0.dev0` на `0.1.0` (выполнено).

## Definition of Done

- analyze HTML работает для FIT и GPX;
- repair dry-run HTML показывает candidates и applied/skipped preview;
- repair write HTML показывает фактический fixed track и FIT diff;
- repair write HTML сравнивает original/course/repaired metrics и перечисляет все
  оставшиеся missing-position runs;
- HTML не соединяет missing GNSS gaps обычной сплошной линией;
- renderer не содержит detection/reconstruction logic;
- no-overwrite, escaping, deterministic output, pinned map dependencies и gap splitting
  покрыты tests;
- public regression suite, type check, lint и format зелёные;
- private Andromeda HTML smoke/performance test проходит при наличии fixtures;
- clean wheel install и CLI smoke проходят;
- README/CLI/architecture/test docs соответствуют реализации;
- свериться с `docs/PRODUCT_SPEC.md` и `docs/MILESTONES.md`.

Task 006B не реализуется внутри M7. Известный residual Andromeda cluster должен быть
виден как detector finding/неразмеченная исходная geometry, но HTML renderer не имеет
права самостоятельно повышать его confidence или создавать repair interval.

## Не делать

- cloud;
- Garmin/COROS/Strava integration;
- OSM map matching и DEM;
- web backend/frontend.

Это отдельная следующая версия.
