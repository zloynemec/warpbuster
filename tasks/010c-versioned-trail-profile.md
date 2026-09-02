# Task 010C — Versioned Pedestrian/Trail Profile

Статус: завершена 2026-09-02.

## Контекст

Task 010B детерминированно превращает immutable OSM Manager snapshot в проверенный
Valhalla graph. Сейчас diagnostic spike выполняет `costing=pedestrian` почти с defaults
Valhalla. Это воспроизводимо технически, но ещё не является заявленной политикой
WarpBuster для восстановления трейлового трека.

Task 010C фиксирует один auditable routing profile. Она не добавляет production route
API и не подключает OSM к FIT repair.

## Цель

Создать версионированный `warpbuster-trail-running-v1`, который:

- явно задаёт все намеренно контролируемые WarpBuster Valhalla pedestrian options;
- разрешает обычные тропы, грунт, ступени и сложность `sac_scale` до T3;
- не избегает естественного набора высоты как ошибки;
- сохраняет hard OSM pedestrian prohibitions;
- отделяет build-time graph policy от request-time route preferences;
- имеет canonical JSON, SHA-256 и documented engine compatibility;
- подтверждён синтетической behavioral matrix, а не только проверкой JSON.

## Архитектурное разделение

### Build profile

`valhalla-pedestrian-graph-v1` определяет содержимое tiles и входит в `graph_id`:

- pedestrian graph включён;
- OSM node/way provenance сохраняется;
- `pedestrian_areas=false`, потому что `pedestrian-routing-v1` не гарантирует relations;
- изменение build option создаёт новый graph ID.

### Routing profile

`warpbuster-trail-running-v1` применяется динамически к Valhalla request. Его изменение
не требует пересборки совместимого graph. Profile ID/hash должны входить в provenance
каждого будущего route response, но не в graph ID.

## Profile v1

Зафиксировать следующие значения без произвольных CLI overrides:

| Option | Значение | Причина |
|---|---:|---|
| `type` | `foot` | обычное pedestrian движение |
| `walking_speed` | `5.1 km/h` | нейтральная engine-модель, не темп спортсмена |
| `max_hiking_difficulty` | `3` | разрешить T1–T3, исключить alpine T4–T6 |
| `use_tracks` | `1.0` | предпочитать tracks при разумном detour |
| `walkway_factor` | `0.8` | умеренно предпочитать footway/path |
| `use_hills` | `1.0` | не обходить естественные трейловые подъёмы |
| `exclude_unpaved` | `false` | грунт является штатным покрытием |
| `use_ferry` | `0.0` | сильно избегать ferry; это не объявляется hard ban |
| `step_penalty` | `30 s` | ступени разрешены, но переход на них не бесплатен |
| `alley_factor` | `2.0` | избегать alley при нормальной альтернативе |
| `driveway_factor` | `5.0` | избегать driveway/private-like service access |
| `use_living_streets` | `0.6` | явное стабильное значение вместо engine default |

Числа являются частью profile schema, имеют единицы и regression tests. Темп, recorded
distance и конкретный спортсмен здесь отсутствуют. Остальные внутренние costing
семантики фиксируются совместимостью только с Valhalla `>=3.8.3,<3.9`; смена engine
family требует нового проверенного профиля.

## Access policy

010C не переписывает OSM access parsing Valhalla и не добавляет custom Lua/fork.
Behavior фиксируется тестами:

- `foot=no` и `access=no` без pedestrian override не дают проход;
- `foot=yes`, `foot=designated` и `foot=permissive` допускаются;
- `access=private`/`destination`, gates, stiles и directional foot access исследуются и
  документируются по факту Valhalla 3.8.3;
- если soft option не гарантирует запрет (например ferry), это явно маркируется как
  post-audit requirement будущей Task 010D, а не выдаётся за hard guarantee.

### Подтверждённое поведение Valhalla 3.8.3

| Сценарий | Результат v1 |
|---|---|
| paved/gravel/ground/mud path | доступен |
| steps | доступны с penalty |
| `sac_scale=demanding_mountain_hiking` (T3) | доступен |
| `sac_scale=alpine_hiking` (T4) | недоступен |
| `foot=no` | недоступен |
| `access=no + foot=yes` | доступен: specific pedestrian override |
| `foot=permissive` | доступен |
| `foot=designated` | доступен |
| `access=private` / `destination` | доступен, когда anchors лежат на самом edge |
| gate / stile без запрета | доступны |
| gate с `access=no` | недоступен |
| короткий steep path | не обходится только из-за подъёма |
| ferry против близкой land alternative | выбирается land route |

Проверка недоступных edge также показала, что Valhalla способен correlated anchor к
другому удалённому компоненту. Это не разрешение запрещённого edge. Maximum snap
distance, показ original/snapped coordinates и отказ от такой корреляции входят в 010D.
Directional pedestrian restrictions не получают отдельной гарантии в 010C: для неё
нужен audited forward/reverse edge response будущей route API, а не вывод по форме
полученной линии.

## Behavioral tests

Использовать небольшие offline OSM fixtures и настоящий Valhalla build/Actor:

- path/track против residential и ограничение абсурдного detour;
- paved/gravel/ground/mud остаются проходимыми;
- `sac_scale` T3 доступен, T4 недоступен;
- explicit foot prohibition;
- steps доступны;
- private/destination/permissive access;
- gate/stile/impassable barriers;
- ferry preference и documented non-guarantee;
- repeatable canonical profile hash и request JSON;
- route profile не меняет 010B graph ID;
- build profile ID/config hash присутствуют в graph manifest, а options — в проверяемом
  config artifact.

Private Andromeda/Orion используются только как comparative probes и не определяют
общую политику.

## CLI

Добавить только inspection, без production route command:

```bash
warpbuster-osm-route profile show
warpbuster-osm-route profile show --json
```

Output содержит profile ID/schema/hash, engine compatibility, costing options, hard
rules, soft preferences и documented limitations.

## Acceptance criteria

- profile представлен immutable typed model и canonical hash;
- request builder всегда вкладывает options в `costing_options.pedestrian`;
- все контролируемые profile values явные, а допустимая engine family зафиксирована;
- synthetic matrix подтверждает заявленные hard rules и preferences;
- неподтверждённое engine behavior документируется как limitation;
- build profile и routing profile не смешиваются в identity/cache;
- CLI inspection и package docs соответствуют фактическому JSON;
- tests Task 010A/010B, Core и OSM Manager остаются зелёными;
- нет production snapping/routes/alternatives, FIT или reconstruction изменений.

## Сознательно не реализуется

- maximum snap distance и anchor ambiguity;
- stable single-route response contract;
- alternatives/overlap/diversity;
- route selection по course, времени, pace или distance;
- post-audit и применение route к ActivityData;
- новый Manager dataset profile с relations;
- pedestrian areas и custom Valhalla fork.
