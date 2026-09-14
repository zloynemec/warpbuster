export function trackSegments(raw = []) {
  const result = [];
  for (const segment of raw) {
    let current = [];
    for (const point of segment) {
      if (Array.isArray(point) && point.length === 2 && point.every(Number.isFinite)
          && Math.abs(point[0]) <= 90 && Math.abs(point[1]) <= 180) current.push(point);
      else { if (current.length) result.push(current); current = []; }
    }
    if (current.length) result.push(current);
  }
  return result;
}

export function createTrackMap(root, tracks, L = window.L) {
  const byId = id => root.getElementById(id);
  const segments = Object.fromEntries(["original", "corrected", "course"].map(key => [key, trackSegments(tracks[key])]));
  const defaults = { original: !segments.corrected.length, corrected: true, course: true };
  if (!L) {
    byId("track-note").textContent = "Не удалось загрузить карту. Обновите страницу.";
    byId("fit-map").disabled = true;
    for (const key of Object.keys(segments)) byId(`show-${key}`).disabled = true;
    return { destroy() {} };
  }
  const map = L.map(byId("track-view"), { preferCanvas: true, scrollWheelZoom: false });
  map.attributionControl.setPrefix('<a href="https://leafletjs.com" rel="noreferrer">Leaflet</a>');
  const tiles = L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19, referrerPolicy: "origin", updateWhenIdle: true, keepBuffer: 1,
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright" rel="noreferrer">OpenStreetMap</a>',
  }).addTo(map);
  tiles.on("tileerror", () => {
    byId("track-note").textContent = "Подложка карты недоступна. Линии треков остаются доступны.";
  });
  const styles = {
    original: { color: "#b54b23", weight: 3, opacity: .8 },
    corrected: { color: "#203e31", weight: 4, opacity: .95 },
    course: { color: "#7664a3", weight: 3, opacity: .85, dashArray: "7 6" },
  };
  const groups = {};
  // Draw the course first; the actual FIT remains readable where the paths coincide.
  for (const key of ["course", "original", "corrected"]) {
    groups[key] = L.featureGroup(segments[key].map(segment => segment.length === 1
      ? L.circleMarker(segment[0], { ...styles[key], radius: 4 })
      : L.polyline(segment, styles[key])));
    const checkbox = byId(`show-${key}`);
    checkbox.disabled = !segments[key].length;
    checkbox.checked = !checkbox.disabled && defaults[key];
    if (checkbox.disabled) checkbox.parentElement.title = "Для этого результата слой недоступен";
    if (checkbox.checked) groups[key].addTo(map);
    checkbox.onchange = () => {
      if (checkbox.checked) groups[key].addTo(map); else map.removeLayer(groups[key]);
    };
  }
  const fit = () => {
    const bounds = L.latLngBounds([]);
    for (const [key, group] of Object.entries(groups)) {
      if (byId(`show-${key}`).checked && group.getBounds().isValid()) bounds.extend(group.getBounds());
    }
    if (bounds.isValid()) map.fitBounds(bounds, { padding: [25, 25], maxZoom: 16 });
  };
  map.setView([0, 0], 2);
  fit();
  byId("fit-map").onclick = fit;
  const resize = new ResizeObserver(() => map.invalidateSize({ pan: false }));
  resize.observe(byId("track-view"));
  return { destroy() { resize.disconnect(); map.remove(); } };
}
