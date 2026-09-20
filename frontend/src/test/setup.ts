import "@testing-library/jest-dom";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

// Cleanup after each test
afterEach(() => {
  cleanup();
  // Runtime backend/profile hints are deliberately session-scoped in the
  // product. They must not leak between Vitest files sharing one jsdom worker,
  // otherwise API tests become order-dependent and can target a prior test's
  // injected Electron backend.
  window.sessionStorage.clear();
  delete window.echo;
});

// Mock window.matchMedia
Object.defineProperty(window, "matchMedia", {
  writable: true,
  value: vi.fn().mockImplementation((query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: vi.fn(),
    removeListener: vi.fn(),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    dispatchEvent: vi.fn(),
  })),
});

// Mock IntersectionObserver
class MockIntersectionObserver {
  observe = vi.fn();
  disconnect = vi.fn();
  unobserve = vi.fn();
}

Object.defineProperty(window, "IntersectionObserver", {
  writable: true,
  value: MockIntersectionObserver,
});

// Mock ResizeObserver
class MockResizeObserver {
  observe = vi.fn();
  disconnect = vi.fn();
  unobserve = vi.fn();
}

Object.defineProperty(window, "ResizeObserver", {
  writable: true,
  value: MockResizeObserver,
});

if (!Element.prototype.hasPointerCapture) {
  Element.prototype.hasPointerCapture = () => false;
}
if (!Element.prototype.setPointerCapture) {
  Element.prototype.setPointerCapture = () => {};
}
if (!Element.prototype.releasePointerCapture) {
  Element.prototype.releasePointerCapture = () => {};
}
if (!Element.prototype.scrollIntoView) {
  Element.prototype.scrollIntoView = () => {};
}

// jsdom 的 Blob 提供 arrayBuffer/text/bytes/slice，却没有 stream()。undici 的
// Response 在把 Blob 当作 body 时会调用 blob.stream()，于是
// `new Response(new Blob([...]))` 抛 "object.stream is not a function"；
// 同为 body 的字符串则正常，所以缺陷只在构造 Blob body 时暴露。
// 浏览器与 Node 原生 Blob 都自带 stream，这里按同一语义补全：整个 blob 作为
// 单个 chunk 推出。放在 setup 而非逐个改用例，是为了让后续新测试不再踩同一个坑。
if (typeof Blob.prototype.stream !== "function") {
  Blob.prototype.stream = function stream(
    this: Blob,
  ): ReadableStream<Uint8Array> {
    return new ReadableStream<Uint8Array>({
      start: async (controller) => {
        controller.enqueue(new Uint8Array(await this.arrayBuffer()));
        controller.close();
      },
    });
  };
}

const localStorageMock = (() => {
  let store: Record<string, string> = {};
  return {
    get length() {
      return Object.keys(store).length;
    },
    key: (index: number) => Object.keys(store)[index] ?? null,
    getItem: (key: string) => (key in store ? store[key] : null),
    setItem: (key: string, value: string) => {
      store[key] = String(value);
    },
    removeItem: (key: string) => {
      delete store[key];
    },
    clear: () => {
      store = {};
    },
  };
})();

Object.defineProperty(window, "localStorage", {
  writable: true,
  value: localStorageMock,
});

// jsdom intentionally has no rendering backend. The landing shell's
// decorative grid only needs a tiny color-sampling surface, so provide that
// bounded contract instead of letting every test emit a getContext warning.
Object.defineProperty(HTMLCanvasElement.prototype, "getContext", {
  configurable: true,
  value: vi.fn((contextId: string) => {
    if (contextId !== "2d") return null;
    return {
      fillStyle: "",
      clearRect: vi.fn(),
      fillRect: vi.fn(),
      getImageData: vi.fn(() => ({
        data: new Uint8ClampedArray([0, 0, 0, 255]),
      })),
    } as unknown as CanvasRenderingContext2D;
  }),
});
