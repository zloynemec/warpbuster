# Task 006A — Trusted Anchor Validation + Mixed GNSS Regions

Статус: завершена.

## Цель

Не позволять course reconstruction считать соседние с `CorruptedInterval` records
надёжными anchors, если они находятся внутри более широкого кластера GNSS jumps или
missing-position gaps.

## Сделать

- directional anchor stability check по records до before-anchor и после after-anchor;
- configurable minimum consecutive normal transitions и bounded scan;
- отдельные причины нестабильности before/after anchor;
- bounded grouping близких `IMPOSSIBLE`, `SUSPICIOUS`, detected interval и missing
  position evidence в `MixedGnssRegion`;
- поиск диагностических внешних stable anchors вокруг mixed region;
- проверку прямого bridge между внешними anchors без course;
- интеграцию safety gate перед GPX course matching;
- console/JSON diagnostics для original anchors и mixed region;
- synthetic tests и private Andromeda acceptance.

## Строгие правила

- course не используется для anchor stability или boundaries mixed region;
- unstable original anchors запрещают HIGH reconstruction candidate;
- наличие stable outer anchors не повышает mixed region до HIGH автоматически;
- mixed region остаётся `MEDIUM/LOW`, `repair_eligible=false`, если не доказано, что все
  содержащиеся plausible coordinates повреждены;
- timestamps и activity records не изменяются;
- scan ограничен config window и не строит O(n²) graph;
- false positive опаснее unresolved reconstruction.

## Acceptance Criteria

- stable windows вокруг обычного single spike проходят safety gate;
- nearby impossible/suspicious transition блокирует соответствующий anchor;
- близкий missing-position gap блокирует anchor с недостаточным normal context;
- unrelated distant anomaly не присоединяется к mixed region;
- основной Andromeda interval сохраняет HIGH course candidate;
- interval `8841..8854` получает оба `anchor_*_unstable`;
- его mixed region содержит clustered jumps/dropouts и диагностические stable outer
  anchors, но остаётся `repair_eligible=false`;
- boundary construction не принимает GPX course;
- все прежние tests и performance target остаются зелёными.

## Не делать

- FIT writer;
- автоматическое применение mixed region;
- расширение corruption boundaries по distance-to-course;
- OSM/DEM reconstruction;
- ослабление all-or-nothing writer eligibility.

Последний пункт ограничивал scope Task 006A. В M6 writer policy позднее изменена на
confidence-threshold partial application; сам mixed region по-прежнему не получает
candidate и всегда пропускается.

## Принятые defaults

- `anchor_stability_min_normal_transitions=15`: достаточно локального контекста для
  типичной секундной записи, но единицей остаются transitions, а не предполагаемые секунды;
- `anchor_stability_scan_max_records=60`: жёсткая граница directional scan;
- `mixed_region_search_max_records=1500`: bounded search вокруг исходного interval;
- `mixed_region_max_clean_gap_records=15`: далёкие независимые anomalies не сливаются.
