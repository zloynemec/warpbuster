export class ApiError extends Error {
  constructor(message, status = 0) { super(message); this.status = status; }
}

export async function requestJson(url, options = {}, timeoutMs = 15000) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(url, { credentials: "same-origin", cache: "no-store", ...options, signal: controller.signal });
    let data;
    try { data = await response.json(); } catch { throw new ApiError("Сервис обработки недоступен.", response.status); }
    if (!response.ok) throw new ApiError(data.detail || "Не удалось выполнить запрос.", response.status);
    return data;
  } catch (error) {
    if (error instanceof ApiError) throw error;
    throw new ApiError("Нет связи с сервером. Проверьте подключение и повторите попытку.");
  } finally { clearTimeout(timer); }
}
