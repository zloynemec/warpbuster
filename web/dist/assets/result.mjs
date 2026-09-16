import { requestJson, ApiError } from "./api.mjs";
import { createResultPoller } from "./polling.mjs";
import { createTrackMap } from "./result-map.mjs";
import { renderRunSummary } from "./run-summary.mjs";

let trackMap;
let runSummary;

const byId = id => document.getElementById(id);
const uid = window.location.pathname.split("/")[2] || "";
const endpoint = `/api/results/${uid}`;
const numeric = new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 2 });
const distance = value => Number.isFinite(value) ? `${numeric.format(value / 1000)} км` : "Нет данных";

function showError(message, { retry = false, title = "Не удалось получить результат" } = {}) {
  byId("waiting").hidden = false;
  byId("report").hidden = true;
  byId("spinner").hidden = true;
  byId("status-title").textContent = title;
  byId("status-message").textContent = message;
  byId("retry-status").hidden = !retry;
  byId("new-upload").hidden = false;
}

function renderReport(data, status) {
  const messages = {
    repaired: ["Трек обработан.", "Повреждённые участки восстановлены по доступным GPX и OSM данным. Временные метки и показания датчиков сохранены."],
    unchanged: ["Без изменений.", "Ядро не выбрало изменений для записи. Исправленный FIT не создавался."],
    unresolved: ["Нужно больше уверенности.", "Безопасного варианта исправления не найдено. Исходная запись не изменялась. Исправленный FIT не создавался."],
  };
  const [title, description] = messages[data.outcome] || messages.unresolved;
  byId("result-title").textContent = title;
  byId("result-description").textContent = description + (data.partial ? " Часть участков осталась невосстановленной." : "");
  byId("metric-changes").textContent = numeric.format(data.fit_diff?.changed_records || 0);
  byId("metric-before").textContent = distance(data.summary.original_distance_m);
  byId("metric-after").textContent = distance(data.summary.corrected_distance_m);
  byId("metric-gpx-gaps").textContent = numeric.format(data.summary.applied_gpx_gaps || 0);
  byId("metric-osm-gaps").textContent = numeric.format(data.summary.applied_osm_gaps || 0);
  byId("metric-unresolved-gaps").textContent = numeric.format(data.summary.unresolved_gaps || 0);
  const osmLabels = {
    not_needed: "OSM не потребовался",
    disabled: "OSM отключён оператором",
    complete: "OSM-проверка завершена",
    partial: "OSM-проверка завершена частично",
    unavailable: "OSM временно недоступен",
  };
  byId("osm-status").textContent = osmLabels[data.osm?.status] || "";
  byId("allocation-note").hidden = !(data.gaps || []).some(gap => gap.estimated);
  byId("osm-warning").hidden = data.osm?.status !== "unavailable";
  byId("osm-warning").textContent = data.osm?.status === "unavailable"
    ? "Карта для дополнительного восстановления была недоступна. Проверенные изменения по GPX сохранены."
    : "";
  const approximate = (data.approximate_osm?.decisions || []).filter(item => item.approximate && item.selected_route_id);
  byId("approximate-warning").hidden = approximate.length === 0;
  byId("approximate-warning").textContent = approximate.length
    ? `OSM-маршрут выбран приблизительно, фактический путь не подтверждён. Выбранные варианты: ${approximate.map(item => `${item.selected_route_id} (${item.selection_mode || "2D"})`).join(", ")}.`
    : "";
  const dem = data.approximate_osm?.dem;
  byId("dem-status").hidden = !dem || dem.status === "disabled" || dem.status === "not_needed";
  byId("dem-status").textContent = dem?.status === "complete"
    ? "DEM-профиль проверен для выбора OSM-маршрута."
    : dem?.status === "unavailable" ? "DEM недоступен; применён безопасный 2D-вариант." : "";
  const altitude = data.altitude_completion;
  byId("altitude-status").hidden = !altitude;
  byId("altitude-status").textContent = altitude?.status === "applied"
    ? `Из DEM заполнена только отсутствующая высота: ${altitude.completed_records} записей. Существующая высота FIT сохранена.`
    : altitude ? `Высота FIT не дополнялась (${altitude.reason || altitude.status}); восстановление координат сохранено.` : "";
  byId("owner-download").hidden = !status.can_download;
  byId("no-file-note").hidden = data.outcome === "repaired";
  byId("share-url").value = `${window.location.origin}/res/${uid}`;
  byId("expires-note").textContent = `Результат доступен до ${new Date(status.expires_at * 1000).toLocaleString("ru-RU")}.`;
  byId("diff-panel").hidden = !data.fit_diff;
  if (data.fit_diff) {
    const diff = data.fit_diff;
    byId("preservation-note").textContent = `Временные метки: ${diff.timestamps_unchanged ? "без изменений" : "изменены"}. Датчики, кроме явно дополненной высоты: ${(diff.non_altitude_sensors_unchanged ?? diff.sensors_unchanged) ? "без изменений" : "изменены"}. Поля разработчика и неизвестные поля: ${diff.developer_fields_unchanged && diff.unknown_fields_unchanged ? "сохранены" : "есть отличия"}.`;
    byId("diff-counts").textContent = `Изменено полей: ${diff.changed_fields}. Координаты: ${diff.coordinate_fields}, дистанция: ${diff.distance_fields}, агрегаты: ${diff.summary_fields}.` + (diff.truncated_changes ? ` Показана часть изменений; ещё ${diff.truncated_changes} не включены в таблицу.` : "");
    byId("diff-rows").replaceChildren();
    for (const change of diff.changes) {
      const row = document.createElement("tr");
      for (const value of [`${change.message}.${change.field}`, change.index, change.before ?? "—", change.after ?? "—"]) {
        const cell = document.createElement("td"); cell.textContent = String(value); row.append(cell);
      }
      byId("diff-rows").append(row);
    }
  }
  byId("waiting").hidden = true;
  byId("report").hidden = false;
  trackMap?.destroy();
  runSummary?.destroy();
  trackMap = createTrackMap(document, data.tracks);
  runSummary = renderRunSummary(document, data.performance);
}

const poller = createResultPoller({
  getStatus: () => requestJson(`${endpoint}/status`),
  onReady: async status => renderReport(await requestJson(endpoint), status),
  onState: status => {
    if (status.status === "failed" || status.status === "expired") {
      showError(status.message || "Срок хранения результата истёк.", { title: "Обработка завершена без результата" });
      return;
    }
    byId("status-title").textContent = status.status === "processing" ? "Проверяем трек." : "Файлы приняты.";
    byId("status-message").textContent = status.status === "processing" ? "Ищем невозможные GPS-скачки и проверяем варианты восстановления." : "Трек в очереди. Обработка начнётся автоматически.";
  },
  onError: (error, terminal) => {
    if (terminal) showError(error.message, { retry: ![403, 404, 410].includes(error.status) });
    else byId("status-message").textContent = "Связь с сервером прервалась. Подключаемся снова…";
  },
});

byId("retry-status").addEventListener("click", () => {
  byId("spinner").hidden = false;
  byId("retry-status").hidden = true; byId("new-upload").hidden = true;
  byId("status-title").textContent = "Проверяем статус…"; poller.start();
});
byId("copy-link").addEventListener("click", async () => {
  try { await navigator.clipboard.writeText(byId("share-url").value); byId("copy-status").textContent = "Ссылка скопирована."; }
  catch { byId("share-url").focus(); byId("share-url").select(); byId("copy-status").textContent = "Скопируйте выделенную ссылку."; }
});
byId("download-fit").addEventListener("click", async () => {
  const button = byId("download-fit"); button.disabled = true;
  byId("download-error").hidden = true;
  try {
    const response = await fetch(`${endpoint}/download`, { credentials: "same-origin", cache: "no-store" });
    if (!response.ok) {
      const error = await response.json(); throw new ApiError(error.detail || "Скачивание недоступно.", response.status);
    }
    const url = URL.createObjectURL(await response.blob());
    const link = document.createElement("a"); link.href = url; link.download = "warpbuster-corrected.fit";
    document.body.append(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url), 30000);
  } catch (error) {
    byId("download-error").textContent = error.message || "Не удалось скачать файл. Повторите попытку.";
    byId("download-error").hidden = false;
    if (error.status === 403) byId("owner-download").hidden = true;
  } finally { button.disabled = false; }
});
window.addEventListener("pagehide", () => poller.stop());
window.addEventListener("pageshow", event => { if (event.persisted) poller.start(); });
if (/^[A-Za-z0-9_-]{43}$/.test(uid)) poller.start();
else showError("Проверьте ссылку на результат.", { title: "Результат не найден" });
