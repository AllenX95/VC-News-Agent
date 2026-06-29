import { ElMessage } from "element-plus";

export type BackendLaunchResult = {
  ok: boolean;
  port?: number;
  base_url?: string;
  owned?: boolean;
  message?: string;
};

const fallbackBaseUrl = import.meta.env.VITE_API_BASE_URL || "http://127.0.0.1:8011";
let apiBaseUrl = fallbackBaseUrl;

export function getApiBaseUrl() {
  return apiBaseUrl;
}

export function setApiBaseUrl(value: string) {
  apiBaseUrl = value.replace(/\/$/, "");
}

export async function ensureBackend(): Promise<BackendLaunchResult> {
  if (window.__TAURI_INTERNALS__) {
    try {
      const { invoke } = await import("@tauri-apps/api/core");
      const result = (await invoke("ensure_backend")) as BackendLaunchResult;
      if (result?.base_url) {
        setApiBaseUrl(result.base_url);
      }
      return result;
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      return { ok: false, base_url: apiBaseUrl, message };
    }
  }

  return { ok: true, base_url: apiBaseUrl, owned: false, message: "browser dev mode" };
}

export async function stopBackend() {
  try {
    await fetch(`${apiBaseUrl}/shutdown`, { method: "POST" });
  } catch (error) {
    console.warn("backend shutdown request failed", error);
  }

  if (!window.__TAURI_INTERNALS__) return;
  const { invoke } = await import("@tauri-apps/api/core");
  await invoke("stop_backend");
}

export async function exitApplication() {
  if (!window.__TAURI_INTERNALS__) {
    window.close();
    return window.closed;
  }

  try {
    const { getCurrentWindow } = await import("@tauri-apps/api/window");
    await getCurrentWindow().close();
    return true;
  } catch (error) {
    console.warn("application close request failed", error);
    window.close();
    return window.closed;
  }
}

export async function openExternalUrl(url: string) {
  if (!url) {
    throw new Error("缺少原文链接");
  }

  if (window.__TAURI_INTERNALS__) {
    const { invoke } = await import("@tauri-apps/api/core");
    await invoke("open_external_url", { url });
    return;
  }

  window.open(url, "_blank", "noopener,noreferrer");
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers || {});
  if (init.body && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  const response = await fetch(`${apiBaseUrl}/api/v1${path}`, { ...init, headers });
  const contentType = response.headers.get("content-type") || "";
  const payload = contentType.includes("application/json") ? await response.json() : await response.text();
  if (!response.ok) {
    const detail = typeof payload === "object" && payload ? payload.detail || payload.message : payload;
    throw new Error(detail || `HTTP ${response.status}`);
  }
  return payload as T;
}

export const api = {
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) }),
  patch: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: "PATCH", body: body === undefined ? undefined : JSON.stringify(body) }),
  delete: <T>(path: string) => request<T>(path, { method: "DELETE" }),
  markdown: async (path: string) => {
    const response = await fetch(`${apiBaseUrl}/api/v1${path}`);
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return response.text();
  },
};

export function notifyError(error: unknown) {
  const message = error instanceof Error ? error.message : String(error);
  ElMessage.error(message);
}
