// Shared public-report presentation and navigation. No activity data in URLs.
import { formatDuration } from "./run-summary.mjs";

const number = new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 2 });
const percent = value => Number.isFinite(value) ? `${number.format(value)}%` : "—";
export const coverageReasons = {
  invalid_record_timestamps: "Временные метки записи не позволяют выполнить расчёт.",
  unresolved_timer_state: "Не удалось однозначно определить паузы записи.",
  no_active_time: "В записи не найдено активного времени.",
};

export function coverageRefused(report) {
  return report.mode === "fit_only" && ["below_threshold", "unavailable"].includes(report.observed_gps_coverage?.status);
}

export function coverageMessage(coverage) {
  if (coverage?.status === "below_threshold") {
    return `Для восстановления без GPX недостаточно сохранившихся GPS-данных: ${percent(coverage.observed_gps_coverage_percent)} активного времени при минимуме ${percent(coverage.minimum_observed_gps_coverage_percent)}. Выбери FIT повторно и добавь GPX маршрута.`;
  }
  if (coverage?.status === "unavailable") {
    return `Не удалось определить покрытие GPS по времени записи. ${coverageReasons[coverage.reason] || ""} Восстановление без GPX не запущено. Выбери FIT повторно и добавь GPX маршрута.`.replace(/ +/g, " ");
  }
  return "";
}

export function coverageDetails(coverage) {
  if (!coverage) return "Для этого отчёта сведения о GPS-покрытии недоступны.";
  if (coverage.status === "not_applicable") return "С GPX порог GPS-покрытия не применяется.";
  return `Исходное GPS-покрытие: ${percent(coverage.observed_gps_coverage_percent)}. Минимум: ${percent(coverage.minimum_observed_gps_coverage_percent)}. Активное время: ${formatDuration(coverage.active_duration_seconds)}. С GPS: ${formatDuration(coverage.observed_gps_duration_seconds)}.`;
}

export function openSubmittedResult(uid, browser = window) {
  if (!/^[A-Za-z0-9_-]{43}$/.test(uid)) throw new Error("Сервер вернул некорректный результат.");
  // The marker belongs to this navigation entry, not to every visit by the owner.
  browser.history.pushState({ warpbusterUpload: uid }, "", `/res/${uid}`);
  browser.location.reload();
}

export function returnToUpload(report, uid, browser = window) {
  if (!coverageRefused(report) || browser.history.state?.warpbusterUpload !== uid) return false;
  const gate = report.observed_gps_coverage;
  browser.history.replaceState({ warpbusterCoverageRefusal: {
    status: gate.status, reason: gate.reason,
    observed_gps_coverage_percent: gate.observed_gps_coverage_percent,
    minimum_observed_gps_coverage_percent: gate.minimum_observed_gps_coverage_percent,
  } }, "", "/fix");
  browser.location.reload();
  return true;
}

export function finishResultNavigation(uid, browser = window) {
  if (browser.history.state?.warpbusterUpload === uid) browser.history.replaceState(null, "");
}

export function endpointMessage(report) {
  if (report.mode !== "fit_only") return "";
  const kinds = new Set((report.gaps || []).filter(gap => gap.action !== "applied").map(gap => gap.kind));
  const prefix = kinds.has("prefix"), suffix = kinds.has("suffix");
  if (prefix && suffix) return "Начало и конец записи остались без координат: для их восстановления не хватает данных.";
  if (prefix) return "Начало записи осталось без координат: для его восстановления не хватает данных.";
  if (suffix) return "Конец записи остался без координат: для его восстановления не хватает данных.";
  return "";
}

const gapKinds = { internal: "Внутренний участок", prefix: "Начало записи", suffix: "Конец записи" };
const gapReasons = {
  no_course: "Нет подходящего пути для восстановления",
  insufficient_observed_gps_coverage: "Недостаточно исходных GPS-данных",
  observed_gps_coverage_unavailable: "Покрытие GPS не определено",
  missing_start_hint: "Недостаточно данных для начала записи",
  missing_finish_hint: "Недостаточно данных для конца записи",
};
export function gapCells(gap) {
  const applied = gap.action === "applied";
  const provider = { gpx: "GPX", osm: "OSM" }[gap.provider];
  const reasons = [...new Set((gap.reasons || []).map(reason => gapReasons[reason]).filter(Boolean))];
  return [String(gap.number), gapKinds[gap.kind] || "Участок", applied ? `Восстановлен${provider ? ` по ${provider}` : ""}` : "Не восстановлен",
    String(applied ? gap.filled_points ?? "—" : gap.unresolved_points ?? "—"),
    applied ? (gap.estimated ? "Распределение по времени" : "Вариант прошёл проверки") : reasons.join(". ") || "Недостаточно данных или уверенности"];
}
