# Task 005 — Safety / False Positive Hardening

## Цель

Сделать detector достаточно консервативным до начала любого repair.

## Добавить synthetic regressions

- real wrong turn на километры;
- out-and-back;
- loop;
- tight switchbacks;
- fast downhill;
- stop/restart;
- irregular sampling;
- long GPS dropout;
- short noisy drift;
- activity with several legitimate pace regimes.

## Главное правило

Distance-to-course не существует в detector.

## Сделать

- пересмотреть confidence rules;
- задокументировать каждый threshold;
- добавить regression tests на false positives;
- добавить diagnostics `-vv`;
- добавить короткий benchmark/performance test.

## Acceptance Criteria

- все plausible trajectories не классифицируются HIGH corrupted;
- wrong-turn fixture CLEAN;
- long spoof island всё ещё HIGH;
- single impossible teleport всё ещё ловится;
- thresholds централизованы;
- нет необъяснённых magic numbers.

## Не делать

Repair всё ещё запрещён.
