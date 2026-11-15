import { cleanup } from "@testing-library/react";
import { afterEach, vi } from "vitest";

// RTL 在 Vitest 非 globals 模式下不会自动 cleanup；每个用例后清 DOM。
afterEach(() => {
  cleanup();
});

// antd 的 Grid.useBreakpoint / 响应式组件依赖 matchMedia；jsdom 默认没有。
Object.defineProperty(window, "matchMedia", {
  writable: true,
  value: (query: string) =>
    ({
      matches: true,
      media: query,
      onchange: null,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
    }) as unknown as MediaQueryList,
});
