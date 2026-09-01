/**
 * Touch Gesture Engine —— 手机触控 → PC鼠标手势映射.
 *
 * 参考 ToDesk / 网易UU远程 的触控交互设计，将手机触控手势
 * 映射为PC鼠标操作，让用户像操作平板一样控制电脑.
 *
 * 手势映射表：
 * ┌─────────────────────┬──────────────────────┐
 * │ 手机触控             │ PC 操作              │
 * ├─────────────────────┼──────────────────────┤
 * │ 单指轻点             │ 鼠标左键点击          │
 * │ 单指双击             │ 鼠标双击              │
 * │ 单指长按             │ 鼠标右键（右键菜单）   │
 * │ 单指拖动             │ 鼠标拖拽              │
 * │ 双指滚动             │ 鼠标滚轮              │
 * │ 双指捏合/张开        │ Ctrl+滚轮（缩放）     │
 * │ 三指轻点             │ 中键点击              │
 * │ 三指上滑             │ Alt+Tab（切换窗口）   │
 * │ 三指下滑             │ 显示桌面              │
 * └─────────────────────┴──────────────────────┘
 *
 * 两种交互模式：
 * 1. 直接触控模式（Direct）：手指点哪里，鼠标就点哪里
 * 2. 触控板模式（Trackpad）：手指相对移动控制鼠标，像笔记本触控板
 *
 * 性能优化：
 * - mouse_move 事件节流（16ms = 60fps）
 * - 长按判定延迟 500ms
 * - 双击判定间隔 300ms
 * - 拖拽阈值 5px
 */

import { useCallback, useRef, useState } from "react";

// ── Types ──────────────────────────────────────────────

export type InteractionMode = "direct" | "trackpad";

export interface GestureEvent {
  action: GestureAction;
  x?: number; // 归一化坐标 [0,1]（direct模式）
  y?: number;
  dx?: number; // 相对移动量（trackpad模式）
  dy?: number;
  deltaX?: number; // 滚轮增量
  deltaY?: number;
  button?: "left" | "right" | "middle";
  key?: string; // 快捷键
  duration_ms?: number;
}

export type GestureAction =
  | "mouse_move"
  | "mouse_down"
  | "mouse_up"
  | "click"
  | "double_click"
  | "right_click"
  | "middle_click"
  | "drag_start"
  | "drag_move"
  | "drag_end"
  | "scroll"
  | "zoom"
  | "key_combo";

export interface TouchGestureConfig {
  /** 交互模式 */
  mode: InteractionMode;
  /** 长按判定时间 (ms) */
  longPressDelay: number;
  /** 双击判定间隔 (ms) */
  doubleTapInterval: number;
  /** 拖拽触发阈值 (px) */
  dragThreshold: number;
  /** mouse_move 节流间隔 (ms) */
  moveThrottle: number;
  /** 触控板模式灵敏度 */
  trackpadSensitivity: number;
  /** 是否显示光标 */
  showCursor: boolean;
}

export const DEFAULT_GESTURE_CONFIG: TouchGestureConfig = {
  mode: "direct",
  longPressDelay: 500,
  doubleTapInterval: 300,
  dragThreshold: 8,
  moveThrottle: 16,
  trackpadSensitivity: 1.5,
  showCursor: true,
};

// ── Gesture Engine ─────────────────────────────────────

export function useTouchGestureEngine(
  config: TouchGestureConfig = DEFAULT_GESTURE_CONFIG,
) {
  const [mode, setMode] = useState<InteractionMode>(config.mode);
  const [cursorPos, setCursorPos] = useState<{ x: number; y: number } | null>(
    null,
  );
  const [isDragging, setIsDragging] = useState(false);
  const [showRipple, setShowRipple] = useState<{
    x: number;
    y: number;
    type: string;
  } | null>(null);

  // 触控状态
  const touchStateRef = useRef({
    // 当前触控点
    touches: new Map<number, { x: number; y: number; startTime: number }>(),
    // 长按定时器
    longPressTimer: null as number | null,
    // 是否已触发长按
    longPressTriggered: false,
    // 是否正在拖拽
    dragging: false,
    // 拖拽起始点
    dragStart: { x: 0, y: 0 },
    // 上次移动时间（节流）
    lastMoveTime: 0,
    // 双击检测
    lastTapTime: 0,
    lastTapPos: { x: 0, y: 0 },
    // 双指初始距离（缩放检测）
    initialPinchDist: 0,
    // 触控板模式：累积偏移
    trackpadAccum: { x: 0, y: 0 },
    // 光标位置
    cursor: { x: 0.5, y: 0.5 },
  });

  // 手势事件回调
  const onGestureRef = useRef<((event: GestureEvent) => void) | null>(null);

  const setOnGesture = useCallback((handler: (event: GestureEvent) => void) => {
    onGestureRef.current = handler;
  }, []);

  const emit = useCallback((event: GestureEvent) => {
    onGestureRef.current?.(event);
  }, []);

  // ── 触控事件处理 ──────────────────────────────────────

  const handleTouchStart = useCallback(
    (e: React.TouchEvent<HTMLCanvasElement>) => {
      e.preventDefault();
      const state = touchStateRef.current;
      const canvas = e.currentTarget;
      const rect = canvas.getBoundingClientRect();

      for (let i = 0; i < e.changedTouches.length; i++) {
        const touch = e.changedTouches[i]!;
        const nx = (touch.clientX - rect.left) / rect.width;
        const ny = (touch.clientY - rect.top) / rect.height;
        state.touches.set(touch.identifier, {
          x: nx,
          y: ny,
          startTime: Date.now(),
        });
      }

      const touchCount = state.touches.size;

      // ── 单指：开始长按检测 ──
      if (touchCount === 1) {
        state.longPressTriggered = false;
        state.dragging = false;
        const firstTouch = Array.from(state.touches.values())[0]!;

        // 直接触控模式：立即移动光标
        if (mode === "direct") {
          state.cursor = { x: firstTouch.x, y: firstTouch.y };
          setCursorPos({ x: firstTouch.x, y: firstTouch.y });
          emit({ action: "mouse_move", x: firstTouch.x, y: firstTouch.y });
        }

        // 长按定时器
        state.longPressTimer = window.setTimeout(() => {
          if (state.touches.size === 1 && !state.dragging) {
            state.longPressTriggered = true;
            // 长按 → 右键
            emit({
              action: "right_click",
              x: state.cursor.x,
              y: state.cursor.y,
            });
            // 涟漪反馈
            setShowRipple({ x: firstTouch.x, y: firstTouch.y, type: "right" });
            setTimeout(() => setShowRipple(null), 400);
          }
        }, config.longPressDelay);
      }

      // ── 双指：记录初始距离（缩放检测） ──
      if (touchCount === 2) {
        // 取消长按
        if (state.longPressTimer) {
          clearTimeout(state.longPressTimer);
          state.longPressTimer = null;
        }
        const pts = Array.from(state.touches.values());
        state.initialPinchDist = Math.hypot(
          pts[0]!.x - pts[1]!.x,
          pts[0]!.y - pts[1]!.y,
        );
      }

      // ── 三指：特殊手势 ──
      if (touchCount === 3) {
        if (state.longPressTimer) {
          clearTimeout(state.longPressTimer);
          state.longPressTimer = null;
        }
      }
    },
    [mode, config.longPressDelay, emit],
  );

  const handleTouchMove = useCallback(
    (e: React.TouchEvent<HTMLCanvasElement>) => {
      e.preventDefault();
      const state = touchStateRef.current;
      const canvas = e.currentTarget;
      const rect = canvas.getBoundingClientRect();
      const now = Date.now();

      // 更新触控点位置
      for (let i = 0; i < e.changedTouches.length; i++) {
        const touch = e.changedTouches[i]!;
        const nx = (touch.clientX - rect.left) / rect.width;
        const ny = (touch.clientY - rect.top) / rect.height;
        const existing = state.touches.get(touch.identifier);
        if (existing) {
          existing.x = nx;
          existing.y = ny;
        }
      }

      const touchCount = state.touches.size;

      // ── 单指移动 ──
      if (touchCount === 1 && !state.longPressTriggered) {
        const firstTouch = Array.from(state.touches.values())[0]!;

        if (mode === "direct") {
          // 直接触控模式：光标跟随手指
          state.cursor = { x: firstTouch.x, y: firstTouch.y };
          setCursorPos({ x: firstTouch.x, y: firstTouch.y });

          // 拖拽判定
          if (!state.dragging) {
            const dragDist = Math.hypot(
              firstTouch.x - state.dragStart.x,
              firstTouch.y - state.dragStart.y,
            );
            if (
              dragDist >
              config.dragThreshold / Math.max(rect.width, rect.height)
            ) {
              state.dragging = true;
              setIsDragging(true);
              emit({
                action: "drag_start",
                x: state.cursor.x,
                y: state.cursor.y,
              });
            }
          }

          if (state.dragging) {
            // 拖拽中
            if (now - state.lastMoveTime >= config.moveThrottle) {
              state.lastMoveTime = now;
              emit({
                action: "drag_move",
                x: state.cursor.x,
                y: state.cursor.y,
              });
            }
          } else {
            // 非拖拽：移动鼠标
            if (now - state.lastMoveTime >= config.moveThrottle) {
              state.lastMoveTime = now;
              emit({
                action: "mouse_move",
                x: state.cursor.x,
                y: state.cursor.y,
              });
            }
          }
        } else {
          // 触控板模式：相对移动
          const prevTouch = state.touches.get(e.changedTouches[0]!.identifier);
          if (prevTouch && now - state.lastMoveTime >= config.moveThrottle) {
            state.lastMoveTime = now;
            // 这里简化处理：用当前位移作为相对移动
            emit({
              action: "mouse_move",
              dx: (firstTouch.x - state.cursor.x) * config.trackpadSensitivity,
              dy: (firstTouch.y - state.cursor.y) * config.trackpadSensitivity,
            });
            // 更新光标
            state.cursor.x = Math.max(
              0,
              Math.min(
                1,
                state.cursor.x +
                  (firstTouch.x - state.cursor.x) * config.trackpadSensitivity,
              ),
            );
            state.cursor.y = Math.max(
              0,
              Math.min(
                1,
                state.cursor.y +
                  (firstTouch.y - state.cursor.y) * config.trackpadSensitivity,
              ),
            );
            setCursorPos({ x: state.cursor.x, y: state.cursor.y });
          }
        }

        // 取消长按（移动超过阈值）
        if (state.longPressTimer) {
          const startDist = Math.hypot(
            firstTouch.x - state.dragStart.x,
            firstTouch.y - state.dragStart.y,
          );
          if (
            startDist >
            config.dragThreshold / Math.max(rect.width, rect.height)
          ) {
            clearTimeout(state.longPressTimer);
            state.longPressTimer = null;
          }
        }
      }

      // ── 双指：滚动/缩放 ──
      if (touchCount === 2) {
        const pts = Array.from(state.touches.values());
        const currentDist = Math.hypot(
          pts[0]!.x - pts[1]!.x,
          pts[0]!.y - pts[1]!.y,
        );
        const midX = (pts[0]!.x + pts[1]!.x) / 2;
        const midY = (pts[0]!.y + pts[1]!.y) / 2;

        if (now - state.lastMoveTime >= config.moveThrottle) {
          state.lastMoveTime = now;

          // 判断是滚动还是缩放
          const distChange = Math.abs(currentDist - state.initialPinchDist);

          if (distChange > 0.02) {
            // 缩放：Ctrl+滚轮
            const zoomDelta = (currentDist - state.initialPinchDist) * 500;
            emit({
              action: "zoom",
              x: midX,
              y: midY,
              deltaY: zoomDelta,
            });
            state.initialPinchDist = currentDist;
          } else {
            // 滚动：上下移动量映射为滚轮
            const scrollDelta = (pts[0]!.y - state.cursor.y) * 300;
            emit({
              action: "scroll",
              x: midX,
              y: midY,
              deltaY: scrollDelta,
            });
          }
        }
      }

      // ── 三指：特殊手势 ──
      if (touchCount === 3) {
        const pts = Array.from(state.touches.values());
        const avgY = pts.reduce((s, p) => s + p.y, 0) / 3;

        if (now - state.lastMoveTime >= 50) {
          state.lastMoveTime = now;
          const dy = avgY - state.cursor.y;

          if (Math.abs(dy) > 0.03) {
            if (dy < 0) {
              // 三指上滑 → Alt+Tab
              emit({ action: "key_combo", key: "alt+tab" });
            } else {
              // 三指下滑 → 显示桌面 (Win+D)
              emit({ action: "key_combo", key: "win+d" });
            }
          }
        }
      }
    },
    [mode, config, emit],
  );

  const handleTouchEnd = useCallback(
    (e: React.TouchEvent<HTMLCanvasElement>) => {
      e.preventDefault();
      const state = touchStateRef.current;

      // 清理触控点
      for (let i = 0; i < e.changedTouches.length; i++) {
        state.touches.delete(e.changedTouches[i]!.identifier);
      }

      // 清除长按定时器
      if (state.longPressTimer) {
        clearTimeout(state.longPressTimer);
        state.longPressTimer = null;
      }

      const touchCount = state.touches.size;

      // ── 所有手指抬起 ──
      if (touchCount === 0) {
        // 拖拽结束
        if (state.dragging) {
          state.dragging = false;
          setIsDragging(false);
          emit({
            action: "drag_end",
            x: state.cursor.x,
            y: state.cursor.y,
          });
          return;
        }

        // 长按已触发，不再处理
        if (state.longPressTriggered) {
          state.longPressTriggered = false;
          return;
        }

        // 单指抬起 → 点击/双击
        const now = Date.now();
        const timeSinceLastTap = now - state.lastTapTime;

        if (timeSinceLastTap < config.doubleTapInterval) {
          // 双击
          emit({
            action: "double_click",
            x: state.cursor.x,
            y: state.cursor.y,
          });
          setShowRipple({
            x: state.cursor.x,
            y: state.cursor.y,
            type: "double",
          });
          setTimeout(() => setShowRipple(null), 400);
          state.lastTapTime = 0; // 重置，防止三击
        } else {
          // 单击
          emit({
            action: "click",
            x: state.cursor.x,
            y: state.cursor.y,
          });
          setShowRipple({ x: state.cursor.x, y: state.cursor.y, type: "tap" });
          setTimeout(() => setShowRipple(null), 400);
          state.lastTapTime = now;
        }
      }
    },
    [config.doubleTapInterval, emit],
  );

  // ── 鼠标事件（PC端也能用） ────────────────────────────

  const handleMouseDown = useCallback(
    (e: React.MouseEvent<HTMLCanvasElement>) => {
      // PC端鼠标操作：直接映射
      const canvas = e.currentTarget;
      const rect = canvas.getBoundingClientRect();
      const nx = (e.clientX - rect.left) / rect.width;
      const ny = (e.clientY - rect.top) / rect.height;

      const state = touchStateRef.current;
      state.cursor = { x: nx, y: ny };
      setCursorPos({ x: nx, y: ny });

      if (e.button === 0) {
        // 左键
        emit({ action: "click", x: nx, y: ny });
      } else if (e.button === 2) {
        // 右键
        emit({ action: "right_click", x: nx, y: ny });
      } else if (e.button === 1) {
        // 中键
        emit({ action: "middle_click", x: nx, y: ny });
      }
    },
    [emit],
  );

  const handleWheel = useCallback(
    (e: React.WheelEvent<HTMLCanvasElement>) => {
      const canvas = e.currentTarget;
      const rect = canvas.getBoundingClientRect();
      const nx = (e.clientX - rect.left) / rect.width;
      const ny = (e.clientY - rect.top) / rect.height;

      emit({
        action: "scroll",
        x: nx,
        y: ny,
        deltaY: e.deltaY,
      });
    },
    [emit],
  );

  const handleContextMenu = useCallback((e: React.MouseEvent) => {
    e.preventDefault(); // 禁用浏览器右键菜单
  }, []);

  return {
    mode,
    setMode,
    cursorPos,
    isDragging,
    showRipple,
    showCursor: config.showCursor,
    setOnGesture,
    handlers: {
      onTouchStart: handleTouchStart,
      onTouchMove: handleTouchMove,
      onTouchEnd: handleTouchEnd,
      onMouseDown: handleMouseDown,
      onWheel: handleWheel,
      onContextMenu: handleContextMenu,
    },
  };
}
