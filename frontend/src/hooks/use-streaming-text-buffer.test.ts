import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useStreamingTextBuffer } from "./use-streaming-text-buffer";

function mockMatchMedia(matches: boolean) {
  window.matchMedia = vi.fn().mockReturnValue({
    matches,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
  }) as unknown as typeof window.matchMedia;
}

describe("useStreamingTextBuffer", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("shows the initial text immediately, then typewriters the growth", () => {
    mockMatchMedia(false);
    const { result, rerender } = renderHook(
      ({ text }: { text: string }) =>
        useStreamingTextBuffer({ targetText: text }),
      { initialProps: { text: "你好" } },
    );
    expect(result.current).toBe("你好");

    const full =
      "你好，这是一段很长的思考内容，用来验证打字机缓冲播放器是否按固定节奏逐步展示，而不会一次性闪烁出来。";
    rerender({ text: full });

    // First tick reveals only a slice, not everything.
    act(() => {
      vi.advanceTimersByTime(40);
    });
    const afterFirstTick = result.current;
    expect(afterFirstTick.length).toBeGreaterThan(2);
    expect(afterFirstTick.length).toBeLessThan(full.length);

    // Catching up: eventually the full text is revealed.
    act(() => {
      vi.advanceTimersByTime(20000);
    });
    expect(result.current).toBe(full);
  });

  it("respects prefers-reduced-motion by showing everything at once", () => {
    mockMatchMedia(true);
    const { result, rerender } = renderHook(
      ({ text }: { text: string }) =>
        useStreamingTextBuffer({ targetText: text }),
      { initialProps: { text: "短" } },
    );
    const full = "这是一段完整的长文本内容，系统要求减少动效时应立即全部展示。";
    rerender({ text: full });
    act(() => {
      vi.advanceTimersByTime(200);
    });
    expect(result.current).toBe(full);
  });

  it("never splits an emoji grapheme cluster mid-glyph", () => {
    mockMatchMedia(false);
    const { result, rerender } = renderHook(
      ({ text }: { text: string }) =>
        useStreamingTextBuffer({ targetText: text }),
      { initialProps: { text: "a" } },
    );
    const emojiText = "🎉🎊✨ 测试思考内容 📌🚀 更多文字".repeat(3);
    rerender({ text: emojiText });

    // Sample the buffer at several points during playback.
    for (let i = 0; i < 30; i++) {
      act(() => {
        vi.advanceTimersByTime(40);
      });
      const shown = result.current;
      // Every code point in the shown text must be complete (no orphan
      // high surrogate at the boundary).
      expect([...shown].join("")).toBe(shown);
      if (shown.length > 0) {
        const last = shown.charCodeAt(shown.length - 1);
        // A trailing high surrogate with no low surrogate would be orphaned.
        if (last >= 0xd800 && last <= 0xdbff) {
          const next = emojiText.charCodeAt(shown.length);
          expect(next >= 0xdc00 && next <= 0xdfff).toBe(true);
        }
      }
    }
    act(() => {
      vi.advanceTimersByTime(20000);
    });
    expect(result.current).toBe(emojiText);
  });

  it("keeps revealing while deltas arrive faster than the display cadence", () => {
    mockMatchMedia(false);
    const { result, rerender } = renderHook(
      ({ text }: { text: string }) =>
        useStreamingTextBuffer({ targetText: text }),
      { initialProps: { text: "起" } },
    );

    const full = "持续到达的高频流式文本不应让显示计时器不断重启而完全停住";
    for (let length = 2; length <= full.length; length += 1) {
      rerender({ text: full.slice(0, length) });
      act(() => {
        vi.advanceTimersByTime(20);
      });
    }

    expect(result.current.length).toBeGreaterThan(1);
    expect(full.startsWith(result.current)).toBe(true);
  });

  it("keeps joined emoji sequences intact", () => {
    mockMatchMedia(false);
    const { result, rerender } = renderHook(
      ({ text }: { text: string }) =>
        useStreamingTextBuffer({
          targetText: text,
          minCharsPerTick: 1,
          maxCharsPerTick: 1,
          // Pin the per-tick step to 1 char: the backlog-proportional
          // lane would otherwise widen and defeat the 1-char setup.
          backlogDivisor: Number.MAX_SAFE_INTEGER,
        }),
      { initialProps: { text: "a" } },
    );
    const family = "👨‍👩‍👧‍👦";
    const full = `a${family}后续内容`;
    rerender({ text: full });
    act(() => {
      vi.advanceTimersByTime(40);
    });

    expect(
      result.current === "a" || result.current.startsWith(`a${family}`),
    ).toBe(true);
  });

  it("drains fast when the stream settles", () => {
    mockMatchMedia(false);
    const { result, rerender } = renderHook(
      ({ text }: { text: string }) =>
        useStreamingTextBuffer({
          targetText: text,
          fastDrainThreshold: 6,
        }),
      { initialProps: { text: "ok" } },
    );
    const tail = "尾巴内容";
    rerender({ text: `已完成，${tail}` });

    // Advance until the backlog is small; the remaining chars drain in one
    // or two ticks thanks to fastDrainThreshold.
    act(() => {
      vi.advanceTimersByTime(4000);
    });
    expect(result.current).toBe(`已完成，${tail}`);
  });

  it("drains the backlog at typewriter pace when the stream finishes", () => {
    mockMatchMedia(false);
    const { result, rerender } = renderHook(
      ({ text, enabled }: { text: string; enabled: boolean }) =>
        useStreamingTextBuffer({
          targetText: text,
          enabled,
          drainOnFinish: true,
        }),
      { initialProps: { text: "好", enabled: true } },
    );
    // Simulate the whole answer arriving in ONE delta + immediate
    // completion (backend delivered it all at once). The buffer must
    // still play it out like a typewriter instead of jumping to full.
    const full =
      "一次性到达的完整回答，流已结束，但播放器应该继续逐字展示而不是瞬间全文。";
    rerender({ text: full, enabled: true });
    rerender({ text: full, enabled: false });
    act(() => {
      vi.advanceTimersByTime(40);
    });
    expect(result.current.length).toBeLessThan(full.length);

    act(() => {
      vi.advanceTimersByTime(20000);
    });
    expect(result.current).toBe(full);
  });

  it("spreads the settled tail across the finish window without a final dump", () => {
    mockMatchMedia(false);
    const { result, rerender } = renderHook(
      ({ text, enabled }: { text: string; enabled: boolean }) =>
        useStreamingTextBuffer({
          targetText: text,
          enabled,
          targetIntervalMs: 40,
          maxFinishDelayMs: 240,
        }),
      { initialProps: { text: "开", enabled: true } },
    );
    const full = `开${"流式内容".repeat(18)}`;
    rerender({ text: full, enabled: true });
    rerender({ text: full, enabled: false });

    const lengths: number[] = [];
    for (let elapsed = 40; elapsed <= 200; elapsed += 40) {
      act(() => {
        vi.advanceTimersByTime(40);
      });
      lengths.push(result.current.length);
    }

    expect(lengths[0]).toBeGreaterThan(1);
    expect(lengths[0]).toBeLessThan(full.length);
    expect(lengths.at(-1)).toBeGreaterThan(lengths[0]!);
    expect(lengths).toEqual([...lengths].sort((a, b) => a - b));

    act(() => {
      vi.advanceTimersByTime(80);
    });
    expect(result.current).toBe(full);
  });

  it("widens the per-tick lane under deep backlog so fast providers cannot outrun it", () => {
    mockMatchMedia(false);
    const { result, rerender } = renderHook(
      ({ text }: { text: string }) =>
        useStreamingTextBuffer({ targetText: text }),
      { initialProps: { text: "起" } },
    );
    // ~1200 chars arrive at once (fast provider burst); with the old fixed
    // 4-char cap the display would need 300 ticks (12s) to catch up. The
    // backlog-scaled cap should close most of the gap within ~5s.
    const burst = `起${"快速模型一次吐出的大段回答内容。".repeat(75)}`;
    rerender({ text: burst });
    act(() => {
      vi.advanceTimersByTime(5000);
    });
    expect(result.current.length).toBeGreaterThan(burst.length * 0.6);
    act(() => {
      vi.advanceTimersByTime(20000);
    });
    expect(result.current).toBe(burst);
  });

  it("reveals the full text immediately on finish when drainOnFinish is off", () => {
    mockMatchMedia(false);
    const { result, rerender } = renderHook(
      ({ text, enabled }: { text: string; enabled: boolean }) =>
        useStreamingTextBuffer({
          targetText: text,
          enabled,
          drainOnFinish: false,
        }),
      { initialProps: { text: "短", enabled: true } },
    );
    const full =
      "完整长文本，drainOnFinish 关闭时流结束应直接全部展示，不走打字机。";
    rerender({ text: full, enabled: true });
    act(() => {
      vi.advanceTimersByTime(40);
    });
    // still typing while enabled
    expect(result.current.length).toBeLessThan(full.length);
    rerender({ text: full, enabled: false });
    act(() => {
      vi.advanceTimersByTime(40);
    });
    expect(result.current).toBe(full);
  });
});
