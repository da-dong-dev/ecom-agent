/**
 * 后端时间统一按 UTC 存储，但 JSON 里是没有时区后缀的 naive 字符串：
 * `2026-11-16T13:06:33.685852`。
 *
 * 浏览器 `new Date(...)` 会把这种字符串当本地时间，东八区会直接少 8 小时。
 * 这里在缺少时区时补 `Z`，再交给浏览器按本地时区展示。
 */
export function parseServerTime(value: string | null | undefined): Date | null {
  if (!value) {
    return null;
  }
  const hasTimezone = /(?:Z|[+-]\d{2}:?\d{2})$/i.test(value);
  const date = new Date(hasTimezone ? value : `${value}Z`);
  return Number.isNaN(date.getTime()) ? null : date;
}

export function formatConversationTime(
  value: string | null | undefined,
): string {
  const date = parseServerTime(value);
  if (!date) {
    return value ?? "";
  }

  const now = new Date();
  const sameDay =
    date.getFullYear() === now.getFullYear() &&
    date.getMonth() === now.getMonth() &&
    date.getDate() === now.getDate();

  const yesterday = new Date(now);
  yesterday.setDate(now.getDate() - 1);
  const isYesterday =
    date.getFullYear() === yesterday.getFullYear() &&
    date.getMonth() === yesterday.getMonth() &&
    date.getDate() === yesterday.getDate();

  const clock = date.toLocaleTimeString("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });

  if (sameDay) {
    return `今天 ${clock}`;
  }
  if (isYesterday) {
    return `昨天 ${clock}`;
  }
  return date.toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}

/** 聊天气泡里的时间：只显示时:分。 */
export function formatMessageTime(
  value: string | null | undefined,
): string {
  const date = parseServerTime(value);
  if (!date) {
    return "";
  }
  return date.toLocaleTimeString("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}
