# Task 021 — Approximate Elevation-aware OSM Reconstruction

Статус: ТЗ, реализация не начата. Предпосылки: Task 012C (автоматический GPX-first +
OSM в Web) и Task 020 (отдельный DEM subsystem) завершены.

## 1. Цель и продуктовая позиция

Восстановить **примерную** траекторию на внутреннем участке активности, где исходных
GNSS-координат нет вообще. Не требуется угадать фактически пройденную дорожку с
точностью до метра. Если доступно несколько физически допустимых маршрутов, полезнее
автоматически записать один обоснованно выбранный вариант с честной пометкой
`approximate`, чем оставить пустоту лишь из-за близких оценок кандидатов.

Это правило касается только отсутствующих координат. Оно **не** разрешает изменять
правдоподобное записанное движение. Главный инвариант остаётся прежним: **Never modify
plausible movement. Repair only demonstrably impossible GNSS data.** Для исходного
`ORIGINAL_MISSING` доказательство ошибочности координат не требуется: координат нет.
Для `INVALIDATED` и `MIXED` новая политика автоматического выбора в 021 не применяется.

Результат не называется «точным», «подтверждённым» или единственно возможным маршрутом.
Valhalla возвращает bounded, не исчерпывающий набор alternatives; победа среди них не
доказывает, что пользователь прошёл именно там.

## 2. Текущее состояние и границы

- `apply_automatic_osm_routes` уже делает GPX-first, ranking OSM-кандидатов,
  allocation и writer preflight при web-политике `MEDIUM`.
- `rank_gap_candidates` оценивает attachment, контекст, направление и независимые
  distance/speed signals. Ничья (`AMBIGUOUS`), малый набор свидетельств
  (`INSUFFICIENT_EVIDENCE`), `minimum_score_margin` и `maximum_recommended_score`
  сейчас влияют на рекомендацию/автоприменение.
- Task 020 даёт проверенный `dem_snapshot_id`, raw/filtered профиль и отдельный
  comparison API. DEM не подключён к Integrity Detector или web job.
- В Task 021 используется существующий FIT → normalize → Integrity Detector → gaps →
  optional Reconstruction pipeline. Course/OSM/DEM не классифицируют GNSS как
  corrupted.

021 **не** переписывает detector, FIT writer или Valhalla graph/cache. Предпочтение
GPX-first сохраняется: уже принятый и прошедший preflight GPX-план не заменяется OSM
только потому, что DEM считает другой вариант легче или короче. Новая политика
помогает выбрать OSM, когда для данного gap нет принятого GPX-кандидата либо он
не прошёл существующие safety gates. Формат пользовательской формы загрузки в 021
не меняется автоматически.

## 3. Область применения

Автоматический approximate fallback допускается для одного gap только если:

1. `gap.origin == ORIGINAL_MISSING`, `original_missing_count == record_count`,
   `invalidated_count == 0`; каждый редактируемый record действительно не имеет
   исходной пригодной позиции.
2. Есть два независимо сохранённых, пригодных anchors в той же continuity component.
   Время и порядок records позволяют существующую allocation без изменения
   timestamps. Односторонние, смешанные и неустойчивые границы не расширяются ради 021.
3. Кандидат получен из verified OSM graph, находится внутри проверенного coverage,
   проходит routing audit и существующие hard geometry/attachment/resource gates.
4. Путь можно распределить по отсутствующим records, каждый новый переход проходит
   физическую проверку с неизменяемыми timestamps, а точный FIT writer preflight
   (включая quantization) принимает весь составной план.
5. Итоговая политика применения допускает `MEDIUM`. Требование `HIGH` не обходится
   и не понижается скрыто. Автоподстановка не получает `HIGH` или `confirmed` только
   благодаря DEM, короткому маршруту либо единственному ответу routing engine.

Если хотя бы один hard gate не выполнен, этот кандидат нельзя записывать. Если после
фильтрации кандидатов нет, gap остаётся unresolved. Нельзя заменять отсутствие
пригодного OSM-пути произвольной прямой или копированием соседних координат под видом
маршрутного восстановления.

Для `INVALIDATED`/`MIXED` текущие консервативные правила остаются без изменений. Их
возможное расширение требует отдельного ТЗ и независимого доказательства corrupted
scope. DEM mismatch сам по себе такого доказательства не даёт.

## 4. Политика выбора: применить лучший безопасный вариант

Разделить два понятия:

- **Hard eligibility** — возможность безопасно записать координаты в заданный gap.
- **Selection certainty** — насколько хорошо один из пригодных маршрутов отличается
  от других и похож ли он на реально пройденный.

Для `ORIGINAL_MISSING` soft ranking не является правом вето. `AMBIGUOUS`, маленький
score margin, неполнота поиска alternatives и недостаток *дополнительных* signals
не превращают hard-eligible кандидата в отказ. `maximum_recommended_score` и
`minimum_observed_evidence_components` остаются диагностическими границами quality
grade, но сами по себе не блокируют approximate fallback. Это изменение задаётся
новой версионированной policy, а не тихой сменой смысла старого advisory API.

Порядок решения для каждого gap:

1. Сохранить принятый GPX-first план, если он прошёл существующий preflight.
2. Для OSM сформировать bounded список кандидатов и отвергнуть тех, кто не
   проходит routing audit, coverage, geometry, anchors и другие ранние hard gates.
   Не делать новые сетевые запросы из-за ничьей.
3. Вычислить существующий 2D score; отсутствие distance/speed signal явно пометить
   `missing`, а не заменить нулём или выдуманной точностью. В стабильном 2D порядке,
   в пределах `maximum_candidate_attempts`, выполнить для кандидатов allocation
   отсутствующих FIT records и FIT writer preflight. Сохранить прошедшие планы:
   именно они, а не просто ответы Valhalla, составляют список для выбора.
4. Взять близкие к лидеру по 2D score планы. Для каждого построить DEM-профиль
   **фактически распределённой траектории** (включая переходы от/к anchors) и
   наложить на него сохранённую во время GPS-пропуска высоту FIT (§5). Если один
   профиль существенно лучше повторяет наблюдавшиеся подъёмы и спуски, поставить
   его первым. DEM не выбирает «самый плоский» путь без наблюдений.
5. Если сравнение DEM неприменимо или различие мало, вернуть прежний 2D порядок.
   Внутри полностью равного ранга использовать connector/total length и стабильный
   `route_id`. Выбрать первый preflight-успешный план. Даже полная ничья заканчивается
   детерминированным выбором, а не `unresolved`, если есть записываемый вариант.
6. Список причин отказа для всех проверенных кандидатов сохранить в audit. Если
   итоговая проверка составного FIT-плана отвергает выбранный вариант, попробовать
   следующий уже проверенный план; не публиковать частично записанный FIT.

Нужен явный `selection_mode`: `gpx_first`, `ranked`, `approximate_tie_break` или
`approximate_low_evidence`. Последние два режима не должны маскироваться под
`unique_route`. Отдельно сохранять `search_complete=false`, если alternatives
неисчерпывающие. Уверенность относится к допустимости изменения отсутствующих
координат, **не** к идентификации исторического маршрута. Для полностью отсутствующей
позиции soft-ничья сама по себе не понижает прошедший hard gates план ниже web-порога
`MEDIUM`; это не утверждение о точности выбранной дороги.

## 5. Роль DEM и altitude evidence

DEM является optional, offline при наличии snapshot. Профиль Task 020 запрашивается
только для прошедших preflight кандидатов из near-best группы; snapshot/profile IDs
и статус должны оставаться в audit. DEM не входит в OSM graph identity и не
пересобирает graph.

DEM evidence имеет три состояния:

- `USABLE`: достаточное покрытие и независимые altitude observations, явно
  сопоставленные с candidate chainage существующей temporal/distance allocation;
  datum известен и совместим для абсолютного residual либо shape-relative метрика
  вычислена по заранее зафиксированной policy с удалением robust offset.
- `UNINFORMATIVE`: нет подтверждённых числовых FIT altitude observations, их
  слишком мало, partial coverage/void не позволяет сравнить пути или все
  кандидаты отличаются менее разрешающей способности evidence. Неизвестный
  vertical datum сам по себе не запрещает shape-relative сравнение.
- `UNAVAILABLE`: snapshot отсутствует, DEM `disabled`, timeout, corruption, engine
  error, несовместимый datum для разрешённых метрик либо бюджет исчерпан.

`UNINFORMATIVE`/`UNAVAILABLE` означают **2D fallback**, не отказ от восстановления.
Конкретный шаг выбора в 021C:

1. Оставить только прошедшие allocation и FIT writer preflight планы в именованном
   `near_best_2d_band` от 2D-лидера. Для **каждого такого плана** сделать отдельный
   вызов `ElevationService.sample(snapshot_id, points)` через Task 020. `points` —
   уже распределённые по FIT records координаты вместе с обоими anchors, то есть
   та же траектория, которая будет записана. Один вызов может содержать несколько
   внутренних batch-запросов к DEM backend; все вызовы читают один snapshot/кэш
   тайлов, а не загружают DEM заново для каждого маршрута. Другие ответы Valhalla
   не профилируются.
2. Распределение missing-coordinate FIT records по каждому маршруту уже выполнено
   на предыдущем шаге: по пригодному recorded-distance progress, иначе integrated
   speed, иначе active time. Координат внутри gap в источнике нет, поэтому GPS map
   matching здесь невозможен. Из результата DEM-вызова взять raw высоту в точках,
   соответствующих тем же record IDs, где в исходном FIT реально есть конечное
   altitude. Не подставлять course altitude вместо отсутствующего наблюдения FIT.
3. Сравнивать кандидатов только по сопоставимому набору record IDs с достаточным
   числом samples, пройденным span и DEM coverage; void не интерполировать. Для
   кандидата с парами `(FIT_i, DEM_i)` вычислить `b = median(FIT_i - DEM_i)` и
   `E = median(abs((FIT_i - DEM_i) - b))` в метрах. Это ошибка **формы** профиля:
   постоянный сдвиг высоты удалён, а подъёмы/спуски остаются. Для сопоставления
   использовать `original_vertex_index` результатов Task 020, не приближённый
   поиск по расстоянию; у densified промежуточных точек этого индекса нет. `E` не
   доказывает историческую идентичность маршрута.
4. Если все сравниваемые профили `USABLE` и выигрыш по `E` превышает именованный
   `minimum_dem_advantage_m`, предпочесть меньший `E` внутри near-best группы.
   Иначе сохранить 2D порядок. При известном совместимом datum абсолютный residual
   можно показать отдельно, но при `DATUM_UNKNOWN`/`DATUM_MISMATCH` его нельзя
   вычислять или использовать для выбора. Никакой DEM-score не преодолевает hard
   gate и не превращает неподходящий 2D-вариант в допустимый.

Пример: два похожих по 2D варианта ведут между теми же anchors. DEM маршрута A
показывает `[100, 100, 100, 100, 100]` м, маршрута B —
`[100, 120, 140, 120, 100]` м. В records без GPS сохранилась FIT-высота
`[230, 250, 270, 250, 230]` м. Абсолютный уровень FIT отличается на 130 м, но
после удаления постоянного сдвига `E_A = 20 м`, `E_B = 0 м`: при достаточном
пороге преимущества выбирается B. Если FIT-высоты внутри gap нет, DEM не знает,
был ли подъём; выбирается лучший 2D-вариант без отказа от repair.

Сырые FIT/course altitude не объявляются EGM96 без достоверного metadata. Наличие
числовой FIT-высоты не доказывает тип датчика или vertical datum; именно поэтому
основной сравнительный показатель не использует абсолютный уровень. Без независимой
высотной серии DEM-профиль показывается в результате, но не выбирает дорогу по
собственной крутизне.

DEM tie-break должен быть bounded и не превосходить решение явных 2D hard checks.
Перед кодом 021C фиксируются именованные пороги minimum aligned samples/span,
coverage, near-best band и minimum DEM advantage в typed config; значения
обосновываются synthetic и доступными реальными probes. Отсутствующие/void samples
не заполняются нулём и не соединяются через gap. Raw профиль используется для
comparison, filtered — для
presentation; smoothing не подменяет наблюдение. Без надёжной сопоставимости
кандидатов DEM не даёт численного преимущества никому.

## 6. Координаты и высота — два независимых решения

Основной результат 021 — 2D-координаты только в `ORIGINAL_MISSING` gap. Timestamps,
сохранённые координаты и FIT metadata остаются неизменными. Наличие DEM **не** даёт
разрешения перезаписать plausibly recorded FIT altitude.

Отдельный optional altitude completion допускается лишь для records с фактически
отсутствующим altitude либо с независимо доказанным corrupted altitude scope.
Нужны отдельные eligibility, datum/provenance checks, `MEDIUM`-или-выше policy,
FIT diff и rollback при writer validation failure. Для неизвестной FIT semantics
значение не трогать. Если altitude completion невозможен, 2D repair всё равно
выполняется; GPX с DEM `<ele>` может быть отдельным side artifact без записи в FIT.

Числа DEM ниже уровня моря допустимы и не считаются ошибкой только из-за знака.
Сравнение с FIT/course остаётся diagnostic evidence, не причиной объявить
существующую координату corrupted.

## 7. API, policy и интеграция

Реализация должна расширять общий Core selection/application path, не дублировать
алгоритм выбора в Web. Ожидаемая граница:

```text
Integrity Detector → independent coordinate mask → ORIGINAL_MISSING gaps
          → GPX-first + verified OSM candidates → 2D ranking/hard gates
          → allocation + FIT writer preflight for bounded attempts
          → DEM profiles for near-best accepted plans (optional)
          → deterministic approximate selection → final validation/audited result
```

Предусмотреть typed request/result для evidence и selection, стабильный policy ID,
canonical policy hash и typed reasons. Изменение policy меняет decision fingerprint,
но не `graph_id` и не `dem_snapshot_id`. Не сериализовать raw Valhalla JSON за
adapter. Базовый `rank_gap_candidates` сохраняет advisory contract: новый режим
автоподстановки явно вызывается только для `ORIGINAL_MISSING`, а не включается
неявно во всех CLI/API.

CLI и Web подключают **один и тот же** Core selection/application path через общий
`run_repair` pipeline; не создавать отдельный Web-алгоритм. CLI `process` и `repair`
при использовании подготовленного OSM graph должны позволять явно выбрать новую
версионированную approximate policy и DEM mode/snapshot через typed configuration.
`--dry-run` показывает то же решение без записи FIT; JSON/HTML/console report и Web
отражают одинаковые policy ID, выбранный route ID, DEM status и причины fallback.
Существующие CLI invocation без нового opt-in сохраняют прежнее поведение до
отдельного решения о смене default. Точные имена новых CLI options зафиксировать
в 021D с тестами парсинга, а не вводить неявно через Web environment.

Web использует ту же policy через текущий worker/orchestration. Его отдельные
обязанности — owner access, public schema/privacy allowlist, job deadline, OSM stage
budget и publish reserve; DEM получает собственный bounded budget и не может съесть
время записи FIT/отчёта. Если DEM этап не успел, оба entry points продолжают 2D
selection. Старый GPX fallback сохраняется. FIT публикуется только после прежней
проверки.

Public schema меняется версионированно и по allowlist. Показывать `approximate`,
provider, selected route ID, selection mode, число рассмотренных/отклонённых вариантов,
доступность DEM, snapshot/profile IDs и предупреждение, что маршрут не подтверждён.
Полная приватная геометрия, FIT records, altitude observations, имена файлов и
детальный native stderr в публичный JSON/логи не попадают. FIT diff обязателен.

## 8. Ресурсные и эксплуатационные ограничения

- Переиспользовать лимиты `GapCandidateRankingConfig`, OSM application и DEM Task 020.
  Количество DEM-профилей не больше числа уже полученных bounded alternatives.
- Никакого мирового DEM prefetch или дополнительных OSM-запросов для разрешения ничьей.
  DEM `auto` — только явная операторская настройка; `offline` и `disabled` работоспособны.
- Все новые пороги, веса, времена, byte/point budgets и правила tie-break находятся
  в typed config, имеют единицы, default, validation, policy hash и тест.
- Invalid/corrupt DEM cache не превращается в cache hit; 2D fallback разрешён только
  после структурированной диагностики и при сохранении независимой 2D-безопасности.
- Никакого LLM/AI в core; одинаковые FIT, GPX, graph, DEM snapshot и policy дают
  одинаковый выбор и отчёт независимо от порядка candidates.

## 9. Подзадачи реализации

- **021A — Decision contract.** Зафиксировать hard/soft gates, typed reasons,
  policy identity, synthetic fixtures и audit schema. Не менять application.
- **021B — Approximate auto-selection.** Для `ORIGINAL_MISSING` выбирать лучший
  hard-safe OSM candidate даже при soft ambiguity, проверять allocation и writer,
  сохранять GPX-first и старую политику для других gap origins. Работает без DEM.
- **021C — Optional DEM evidence.** Bounded candidate sampling, candidate-local
  alignment FIT altitude с DEM, offset-neutral ошибка формы и её применение для
  выбора между близкими 2D alternatives; `USABLE/UNINFORMATIVE/UNAVAILABLE`;
  отсутствие DEM возвращает к проверенному выбору 021B.
- **021D — CLI/Web integration и отчёт.** Подключить один Core path к `repair`/
  `process` CLI и Web с явным opt-in, DEM mode/snapshot configuration, dry-run,
  одинаковой семантикой решения и FIT diff. Для Web дополнительно проверить
  deadline, owner access и privacy-safe public schema; выполнить CLI offline и
  Web production regressions. Никакой отдельной Web-selection policy.
- **021E — Optional altitude completion.** Отдельный altitude decision при
  доказанной пустоте/порче, проверки datum/provenance и FIT writer validation;
  невозможность заполнить altitude не отменяет 2D repair в CLI или Web.

021E не является предпосылкой для 021D: приблизительное восстановление координат
должно работать в CLI и Web до появления optional altitude completion.

## 10. Обязательные тесты

1. Два близких пригодных OSM-варианта, DEM unavailable: выбран один стабильный
   `approximate_tie_break`; повтор и перестановка alternatives дают тот же route ID.
2. Только один hard-safe маршрут при soft `INSUFFICIENT_EVIDENCE`: он применяется,
   результат честно помечен approximate/low evidence.
3. Один candidate не проходит speed/anchor/quantized FIT preflight: выбирается
   следующий; если все провалены — unresolved, исходный FIT не изменён.
   DEM не вызывается для отвергнутых кандидатов и не подменяет preflight.
4. Исходно правдоподобные координаты, `INVALIDATED` и `MIXED` не получают новую
   approximate policy; detector и coordinate mask инвариантны при смене DEM.
5. Принятый GPX сохраняет GPX-first даже при более высоком DEM-score у OSM.
6. DEM full/partial/void/missing, timeout и corrupt cache: ошибочная/неполная DEM
   информация не блокирует допустимый 2D repair.
7. Известный совместимый datum, `DATUM_UNKNOWN`, `DATUM_MISMATCH`, отсутствие
   altitude и несопоставленные observations; только разрешённые пары влияют на
   DEM tie-break. Без независимых наблюдений «самый пологий» не получает бонус.
   Синтетическая развилка «ровно против подъёма» из §5 меняет победителя в пользу
   подъёма; добавление постоянного сдвига ко всей FIT-высоте не меняет результат.
   При преимуществе меньше `minimum_dem_advantage_m`, partial/void coverage или
   несовпадающих наборах records применяется прежний 2D порядок.
   Два near-best плана получают два отдельных вызова sample с одним snapshot;
   соответствие FIT records DEM samples проверено по `original_vertex_index`.
8. Pause/active-time allocation, long gap, distance stream unknown/vendor-neutral,
   incomplete Valhalla search и deterministic tie при равных scores.
9. Отсутствующий FIT altitude отдельно от координатного gap; сохранённый plausible
   altitude byte-identical, timestamps byte-identical, FIT diff ограничен областью.
10. CLI `repair`/`process`: явный opt-in и старый default, offline DEM snapshot,
    `--dry-run`, JSON/HTML/console reports и идентичное Core-решение при тех же
    входах/policy; неверные сочетания options отвергаются без записи FIT.
11. Web: schema/privacy allowlist, owner access, deadline/publish reserve, GPX
    fallback и старые результаты. Повторить реальные пары 012C и отдельные
    приватные missing-position примеры без коммита FIT в публичный репозиторий.

## 11. Acceptance criteria

- [ ] Для original-missing gap с ≥1 hard-safe маршрутом soft ambiguity больше не
      оставляет gap пустым; выбор детерминирован и отмечен `approximate`.
- [ ] Hard gates и writer preflight не ослаблены; plausible movement и другие
      origins не затронуты.
- [ ] DEM улучшает различение только при пригодном независимом evidence; отсутствие,
      void, timeout и datum mismatch не блокируют 2D repair.
- [ ] GPX-first и existing CLI/Web fallback сохранены; один Core-алгоритм доступен
      через оба entry points, FIT публикуется только после validation и FIT diff.
- [ ] Altitude completion независимо gated и никогда не перезаписывает plausible
      FIT altitude; невозможность completion не отменяет 2D repair.
- [ ] Policy/config/audit/versioning и privacy contract документированы; решение
      повторяемо при перестановке candidates.
- [ ] Unit, integration, CLI, Web, offline, performance, resource, privacy и реальные
      missing-position regressions зелёные вместе с Core и OSM Manager/Routing.

## 12. Не входит в Task 021

- Метровая точность или утверждение о фактически пройденной улице.
- Изменение Integrity Detector на основании GPX/OSM/DEM.
- Исправление правдоподобного позиционированного GNSS по отклонению от карты.
- Автоматическое снятие hard safety gates ради «обязательного» результата.
- Неограниченный поиск/скачивание alternatives либо мировой DEM cache.
- Смена timestamps, лечение скорости добавлением времени, FIT → GPX → FIT pipeline.
- Автоматическая запись DEM-высоты поверх достоверных FIT altitude samples.
