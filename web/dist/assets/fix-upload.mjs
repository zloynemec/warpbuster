import { requestJson } from "./api.mjs";

export function validateSelection(files, extension, maxBytes = Infinity) {
  if (files.length !== 1) return "Добавь один файл в это поле.";
  const file = files[0];
  if (!file.name.toLowerCase().endsWith(`.${extension}`)) {
    return `Здесь нужен файл .${extension.toUpperCase()}. Проверь формат экспорта.`;
  }
  if (file.size === 0) return "Файл пустой. Экспортируй его ещё раз.";
  if (file.size > maxBytes) return `Файл слишком большой. Максимум — ${formatFileSize(maxBytes)}.`;
  return "";
}

export function formatFileSize(bytes) {
  const megabyte = 1024 * 1024;
  const number = new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 1 });
  return bytes >= megabyte
    ? `${number.format(bytes / megabyte)} МБ`
    : `${number.format(Math.max(1, Math.ceil(bytes / 1024)))} КБ`;
}

export function initUploadPage(root) {
  const selected = new Map();
  const count = root.getElementById("ready-count");
  const button = root.getElementById("analyze-button");
  const note = root.getElementById("analysis-note");
  const submitError = root.getElementById("submit-error");
  const retry = root.getElementById("service-retry");
  let available = false;
  let submitting = false;
  let maxBytes = Infinity;
  let submissionKey = null;
  const updateCount = () => {
    count.textContent = selected.size === 2 ? "Оба файла выбраны" : `Выбрано ${selected.size} из 2`;
    button.disabled = submitting || !available || selected.size !== 2;
  };

  for (const extension of ["fit", "gpx"]) {
    const input = root.getElementById(`${extension}-file`);
    const card = root.querySelector(`[data-file-card="${extension}"]`);
    const zone = card.querySelector(".drop-zone");
    const row = card.querySelector(".selection-row");
    const name = root.getElementById(`${extension}-selected`);
    const error = root.getElementById(`${extension}-error`);
    const action = root.getElementById(`${extension}-action`);

    const clear = () => {
      submissionKey = null;
      selected.delete(extension);
      input.value = "";
      input.removeAttribute("aria-invalid");
      row.hidden = true;
      name.textContent = "";
      error.hidden = true;
      error.textContent = "";
      card.dataset.selected = "false";
      action.textContent = `Выбрать ${extension.toUpperCase()}-файл`;
      updateCount();
    };

    const choose = (files) => {
      if (submitting) return;
      const message = validateSelection(files, extension, maxBytes);
      clear();
      if (message) {
        error.textContent = message;
        error.hidden = false;
        input.setAttribute("aria-invalid", "true");
        return;
      }
      const file = files[0];
      selected.set(extension, file);
      name.textContent = `${file.name} · ${formatFileSize(file.size)}`;
      row.hidden = false;
      card.dataset.selected = "true";
      action.textContent = `Заменить ${extension.toUpperCase()}-файл`;
      updateCount();
    };

    input.addEventListener("change", () => {
      // Cancelling the picker keeps the previous selection.
      if (input.files.length) choose(Array.from(input.files));
    });
    card.querySelector("[data-remove]").addEventListener("click", () => {
      if (submitting) return;
      clear();
      input.focus();
    });
    zone.addEventListener("dragover", (event) => {
      event.preventDefault();
      zone.dataset.dragging = "true";
    });
    zone.addEventListener("dragleave", (event) => {
      if (!zone.contains(event.relatedTarget)) delete zone.dataset.dragging;
    });
    zone.addEventListener("drop", (event) => {
      event.preventDefault();
      delete zone.dataset.dragging;
      choose(Array.from(event.dataTransfer?.files ?? []));
    });
    input.disabled = false;
  }

  // Dropping a file outside either card must not navigate away and lose the selection.
  for (const eventName of ["dragover", "drop"]) {
    root.addEventListener(eventName, (event) => {
      if (Array.from(event.dataTransfer?.types ?? []).includes("Files")) event.preventDefault();
    });
  }

  const connect = async () => {
    available = false;
    retry.hidden = true;
    updateCount();
    note.textContent = "Подключаемся к сервису обработки…";
    try {
      const session = await requestJson("/api/session", { headers: { "X-WarpBuster-Request": "1" } });
      maxBytes = session.file_limit_bytes;
      available = true;
      note.textContent = `До ${formatFileSize(maxBytes)} на файл. Результат хранится ${session.retention_days} дней.`;
    } catch (error) {
      note.textContent = error.message;
      retry.hidden = false;
    }
    updateCount();
  };
  retry.addEventListener("click", connect);
  button.addEventListener("click", async () => {
    if (button.disabled) return;
    for (const extension of ["fit", "gpx"]) {
      const message = validateSelection([selected.get(extension)], extension, maxBytes);
      if (message) { submitError.textContent = message; submitError.hidden = false; return; }
    }
    submissionKey ??= crypto.randomUUID();
    submitting = true;
    updateCount();
    button.textContent = "Загружаем файлы…";
    submitError.hidden = true;
    const inputs = root.querySelectorAll('input[type="file"], [data-remove]');
    inputs.forEach(input => { input.disabled = true; });
    try {
      const form = new FormData();
      form.append("activity", selected.get("fit"));
      form.append("course", selected.get("gpx"));
      const data = await requestJson("/api/jobs", {
        method: "POST", body: form,
        headers: { "X-WarpBuster-Request": "1", "X-WarpBuster-Upload-ID": submissionKey },
      }, 75000);
      if (!/^[A-Za-z0-9_-]{43}$/.test(data.uid)) throw new Error("Сервер вернул некорректный результат.");
      window.location.assign(`/res/${data.uid}`);
    } catch (error) {
      submitError.textContent = error.message;
      submitError.hidden = false;
      if (error.status === 401) { available = false; retry.hidden = false; }
    } finally {
      submitting = false;
      button.textContent = "Проверить трек";
      inputs.forEach(input => { input.disabled = false; });
      updateCount();
    }
  });
  connect();
}

if (typeof document !== "undefined") initUploadPage(document);
