/** 后端金额单位是「分」；只在前端展示时转成元。 */
export function formatAmount(fen: number): string {
  return `¥${(fen / 100).toFixed(2)}`;
}
