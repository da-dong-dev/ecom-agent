/** 会话级本地存储：localStorage 不可用时静默失败，不影响聊天。 */

export function getStorage(): Storage | null {
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

export function rememberClientMessageId(clientMessageId: string): void {
  try {
    getStorage()?.setItem("lastClientMessageId", clientMessageId);
  } catch {
    // localStorage 不可用时只影响调试，不影响发送。
  }
}

/** 同时写 localStorage 与 URL `?c=`，刷新后优先从 URL 恢复。 */
export function rememberConversationId(cid: number): void {
  try {
    getStorage()?.setItem("lastConversationId", String(cid));
  } catch {
    // 忽略存储失败。
  }
  try {
    const url = new URL(window.location.href);
    url.searchParams.set("c", String(cid));
    window.history.replaceState({}, "", url);
  } catch {
    // 忽略 URL 更新失败。
  }
}

export function forgetConversationId(): void {
  try {
    getStorage()?.removeItem("lastConversationId");
  } catch {
    // 忽略存储失败。
  }
  try {
    const url = new URL(window.location.href);
    url.searchParams.delete("c");
    window.history.replaceState({}, "", url);
  } catch {
    // 忽略 URL 更新失败。
  }
}
