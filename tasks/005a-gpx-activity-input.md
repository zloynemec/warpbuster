# Task 005A — GPX Activity Input

## Цель

Разрешить использовать GPX как самостоятельный input для `inspect` и `analyze`, не
конвертируя его в FIT и не смешивая activity input с GPX course из Task 006.

## Сделать

- отдельный GPX activity reader;
- общий dispatcher для `.fit` и `.gpx`;
- нормализацию `trk/trkseg/trkpt` в `ActivityData`;
- чтение latitude, longitude, time и elevation;
- консервативное распознавание running/trail running из `<type>`;
- сохранение границ `trkseg` как границ физической непрерывности;
- `warpbuster inspect activity.gpx`;
- `warpbuster analyze activity.gpx`;
- явные ошибки для malformed/unsupported input;
- unit, CLI и FIT regression tests.

## Строгие правила

- GPX activity не является course и не влияет на detector иначе, чем через
  нормализованные observations;
- разные `trkseg` нельзя соединять synthetic transition;
- отсутствующий timestamp даёт `UNKNOWN`, а не corruption;
- неизвестный `<type>` использует generic profile;
- FIT reader и FIT preservation semantics не ослабляются;
- GPX не преобразуется в FIT.

## Acceptance Criteria

- существующие FIT tests и Andromeda acceptance остаются зелёными;
- valid GPX доступен в `inspect` и `analyze`;
- clean running GPX классифицируется `CLEAN`;
- impossible running teleport обнаруживается как `CORRUPTED / HIGH`;
- большой разрыв между разными `trkseg` не считается teleport;
- GPX без времени классифицируется `UNKNOWN`;
- invalid GPX и неизвестное расширение возвращают exit code `2`;
- GPX workflow не создаёт FIT-файлов.

## Не делать

- GPX course matching;
- reconstruction/repair;
- FIT writer;
- интерпретацию vendor-specific GPX extensions;
- импорт route (`rte`) или waypoint (`wpt`) как activity track.
