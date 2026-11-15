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
  if (status === 429) {
    return new ApiError(429, "busy", "操作太频繁，请稍后再试");
  }
  return new ApiError(status, "internal_error", "服务暂时不可用，请稍后重试");
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
    throw errorForStatus(response.status);
  }
  return response;
}
