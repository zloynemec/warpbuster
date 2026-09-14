const number = new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 1 });
export const formatMetric = value => Number.isFinite(value) ? number.format(value) : "—";
export function formatDuration(seconds) {
  if (!Number.isFinite(seconds) || seconds < 0) return "—";
  const whole = Math.round(seconds), hours = Math.floor(whole / 3600);
  const minutes = Math.floor(whole / 60) % 60, rest = whole % 60;
  return hours ? `${hours}:${String(minutes).padStart(2, "0")}:${String(rest).padStart(2, "0")}`
    : `${minutes}:${String(rest).padStart(2, "0")}`;
}
export function formatPace(seconds) {
  if (!Number.isFinite(seconds) || seconds < 0) return "—";
  const whole = Math.round(seconds);
  return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, "0")}`;
}
export function splitLabel(split) {
  return split.complete_kilometre ? String(split.index)
    : `${number.format(split.start_distance_m / 1000)}–${number.format(split.end_distance_m / 1000)}`;
}

function svgElement(svg, name, attributes, text) {
  const element = svg.ownerDocument.createElementNS("http://www.w3.org/2000/svg", name);
  for (const [key, value] of Object.entries(attributes)) element.setAttribute(key, value);
  if (text !== undefined) element.textContent = text;
  return element;
}

export function drawSplitChart(svg, splits, elevation = false) {
  svg.replaceChildren();
  const width = Math.max(300, svg.clientWidth || 900), height = 240;
  svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
  const left = 55, right = width - 15, top = 20, bottom = height - 45;
  const fields = elevation ? ["ascent_m", "descent_m"] : ["pace_seconds_per_km"];
  const values = splits.flatMap(split => fields.map(field => split[field])).filter(Number.isFinite);
  const add = (name, attrs, text) => { const element = svgElement(svg, name, attrs, text); svg.append(element); return element; };
  if (!values.length) { add("text", { x: width / 2, y: 110, "text-anchor": "middle", class: "chart-label" }, "Нет данных"); return; }
  let max = 1;
  for (const value of values) max = Math.max(max, value);
  const y = value => bottom - value / max * (bottom - top);
  for (let i = 0; i <= 4; i++) {
    const value = max * i / 4;
    add("line", { x1: left, x2: right, y1: y(value), y2: y(value), class: "chart-grid" });
    add("text", { x: left - 8, y: y(value) + 4, "text-anchor": "end", class: "chart-label" }, elevation ? number.format(value) : formatPace(value));
  }
  const slot = (right - left) / splits.length, step = Math.max(1, Math.ceil(splits.length / Math.max(2, Math.floor(width / 70))));
  splits.forEach((split, index) => {
    const center = left + slot * (index + .5);
    fields.forEach((field, series) => {
      const value = split[field];
      if (!Number.isFinite(value)) return;
      const barWidth = Math.min(elevation ? 20 : 38, slot * (elevation ? .3 : .65));
      const x = elevation ? center + (series ? slot * .03 : -barWidth - slot * .03) : center - barWidth / 2;
      const rect = add("rect", { x, y: y(value), width: barWidth, height: Math.max(1, bottom - y(value)), rx: 2,
        fill: elevation && series ? "#b54b23" : "#203e31" });
      rect.append(svgElement(svg, "title", {}, `${splitLabel(split)} км: ${elevation ? `${series ? "сброс" : "набор"} ${formatMetric(value)} м` : `${formatPace(value)} мин/км`}`));
    });
    if (index % step === 0 || index === splits.length - 1) {
      add("text", { x: center, y: bottom + 22, "text-anchor": "middle", class: "chart-label" }, splitLabel(split));
    }
  });
  add("text", { x: (left + right) / 2, y: height - 3, "text-anchor": "middle", class: "chart-label" }, "Километр");
}

export function renderRunSummary(root, data) {
  const byId = id => root.getElementById(id);
  byId("run-empty").hidden = Boolean(data);
  byId("run-content").hidden = !data;
  byId("run-source").textContent = data ? (data.source === "corrected" ? "По исправленному FIT" : "По исходному FIT") : "";
  if (!data) return { destroy() {} };
  byId("distance-warning").hidden = !["uncertain", "unknown"].includes(data.distance_quality);
  byId("distance-warning").textContent = data.distance_quality === "unknown"
    ? "Для этого ранее созданного результата качество дистанции и темпа не определено."
    : "Дистанция и темп могут быть неточными: часть данных осталась неопределённой.";
  byId("run-pace").textContent = Number.isFinite(data.average_pace_seconds_per_km) ? `${formatPace(data.average_pace_seconds_per_km)} /км` : "—";
  byId("run-time").textContent = formatDuration(data.timer_duration_seconds);
  byId("run-ascent").textContent = Number.isFinite(data.total_ascent_m) ? `↑ ${formatMetric(data.total_ascent_m)} м` : "—";
  byId("run-descent").textContent = Number.isFinite(data.total_descent_m) ? `↓ ${formatMetric(data.total_descent_m)} м` : "—";
  byId("cadence-heading").textContent = data.cadence_unit === "steps_per_minute" ? "Каденс, шаг/мин" : "Каденс, /мин";
  const splits = data.splits || [];
  byId("splits-empty").hidden = splits.length > 0;
  byId("splits-content").hidden = !splits.length;
  byId("split-rows").replaceChildren();
  const fragment = root.createDocumentFragment();
  for (const split of splits) {
    const row = root.createElement("tr");
    const values = [splitLabel(split), `${formatMetric(split.distance_m)} м`, formatDuration(split.elapsed_seconds),
      formatPace(split.pace_seconds_per_km), `${formatMetric(split.ascent_m)} / ${formatMetric(split.descent_m)} м`,
      formatMetric(split.average_heart_rate_bpm), formatMetric(split.average_cadence),
      Number.isFinite(split.coordinate_coverage_ratio) ? `${Math.round(split.coordinate_coverage_ratio * 100)}%` : "—"];
    for (const value of values) { const cell = root.createElement("td"); cell.textContent = value; row.append(cell); }
    fragment.append(row);
  }
  byId("split-rows").append(fragment);
  const render = () => {
    drawSplitChart(byId("pace-chart"), splits);
    drawSplitChart(byId("elevation-chart"), splits, true);
  };
  render();
  const observer = new ResizeObserver(render);
  observer.observe(byId("run-summary"));
  return { destroy() { observer.disconnect(); } };
}
