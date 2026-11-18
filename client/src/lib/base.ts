/** 请求基础层：baseURL、trace_id、错误映射。 */

export const BASE = import.meta.env.VITE_API_BASE ?? "";

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly code: string,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

/** HTTP 状态 → 前端可处理的错误码。 */
export function errorForStatus(status: number): ApiError {
  if (status === 404) {
    return new ApiError(404, "not_found", "会话不存在");
  }
  if (status === 409) {
    return new ApiError(409, "conflict", "当前状态不可操作");
  }
  if (status === 422) {
    return new ApiError(422, "invalid_arg", "参数不合法");
  }
  if (status === 429) {
    return new ApiError(429, "busy", "操作太频繁，请稍后再试");
  }
  if (status === 502) {
    return new ApiError(502, "sync_failed", "知识库同步失败，请重试");
  }
  return new ApiError(status, "internal_error", "服务暂时不可用，请稍后重试");
}

type ErrorDetail = {
  detail?: string | { code?: string; message?: string };
};

function _errorFromResponse(status: number, payload: unknown): ApiError {
  const fallback = errorForStatus(status);
  if (typeof payload !== "object" || payload === null) {
    return fallback;
  }
  const detail = (payload as ErrorDetail).detail;
  if (typeof detail === "string") {
    return new ApiError(status, fallback.code, detail);
  }
  if (detail && typeof detail === "object") {
    return new ApiError(
      status,
      detail.code ?? fallback.code,
      detail.message ?? fallback.message,
    );
  }
  return fallback;
}

/** 每条请求一个 trace_id；测试环境没有 randomUUID 时退化为随机串。 */
export function newTraceId(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  return `trace-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

/** JSON 请求封装；SSE 走 sse.ts，不用这里。 */
export async function request(
  path: string,
  init: RequestInit = {},
): Promise<Response> {
  const headers = new Headers(init.headers);
  if (!headers.has("X-Trace-Id")) {
    headers.set("X-Trace-Id", newTraceId());
  }

  const response = await fetch(BASE + path, { ...init, headers });
  if (!response.ok) {
    let payload: unknown = null;
    try {
      payload = await response.clone().json();
    } catch {
      // 非 JSON 错误体：用状态码默认文案
    }
    throw _errorFromResponse(response.status, payload);
  }
  return response;
}
