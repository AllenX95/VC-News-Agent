import { ElMessage } from "element-plus";

export type BackendLaunchResult = {
  ok: boolean;
  base_url?: string;
  message?: string;
};

const fallbackBaseUrl =
  import.meta.env.VITE_API_BASE_URL || (import.meta.env.DEV ? "http://127.0.0.1:8011" : window.location.origin);
let apiBaseUrl = fallbackBaseUrl;

export function getApiBaseUrl() {
  return apiBaseUrl;
}

export function setApiBaseUrl(value: string) {
  apiBaseUrl = value.replace(/\/$/, "");
}

/** Build the backend URL for a self-contained automated HTML daily report. */
export function getAutomationLatestReportUrl(targetDate?: string | null) {
  const url = new URL(`${apiBaseUrl}/api/v1/automation/latest-report`);
  if (targetDate) url.searchParams.set("date", targetDate);
  return url.toString();
}

export async function ensureBackend(): Promise<BackendLaunchResult> {
  try {
    const response = await fetch(`${apiBaseUrl}/api/v1/app-info`);
    if (!response.ok) {
      return { ok: false, base_url: apiBaseUrl, message: `后端健康检查失败：HTTP ${response.status}` };
    }
    const payload = await response.json();
    const ok = payload?.app_id === "ai-investment-agent";
    return {
      ok,
      base_url: apiBaseUrl,
      message: ok ? "Web 后端已连接" : "当前端口不是 VC-news-agent-AI 后端",
    };
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    return { ok: false, base_url: apiBaseUrl, message };
  }
}

export async function stopBackend() {
  const response = await fetch(`${apiBaseUrl}/shutdown`, { method: "POST" });
  if (!response.ok) {
    throw new Error(`关闭后端失败：HTTP ${response.status}`);
  }
}

export async function openExternalUrl(url: string) {
  if (!url) {
    throw new Error("缺少原文链接");
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
  latestReportUrl: getAutomationLatestReportUrl,
};

export function notifyError(error: unknown) {
  const message = error instanceof Error ? error.message : String(error);
  ElMessage.error(message);
}
