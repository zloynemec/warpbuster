# Task 004 — Long Spoofing Islands + Bridge Plausibility

## Цель

Обнаруживать длительный ложный GNSS-остров, внутреннее движение которого само выглядит правдоподобно.

## Ключевой паттерн

A → X impossible  
X ... Y plausible/unknown  
Y → B impossible  
A → B за всё elapsed time plausible

## Сделать

- поиск candidate exit после impossible entry;
- bridge plausibility;
- bounded reachability/skip search;
- grouping в `CorruptedInterval`;
- confidence + reasons;
- interval boundary model;
- pruning/performance limits.

## Не делать

- полный O(n²) graph;
- GPX/course;
- reconstruction;
- FIT write.

## Synthetic acceptance

Spoof island:
- teleport out;
- 20+ минут плавного fake movement;
- teleport back;
- detector маркирует весь island, а не только крайние точки.

## Andromeda acceptance

Без `--course`:
- основной incident найден как единый interval;
- HIGH confidence;
- bridge plausible;
- boundaries на первом этапе допускают ~±30 s.

## Performance

~20k records должен анализироваться практически интерактивно; target <5 s.

## Acceptance Criteria

Все вышеперечисленное + зелёные M0–M3 tests.
