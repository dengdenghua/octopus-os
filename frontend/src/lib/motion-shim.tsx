import {
  createElement,
  forwardRef,
  Fragment,
  useEffect,
  useRef,
  useState,
  type ComponentType,
  type CSSProperties,
  type ElementType,
  type ReactNode,
} from "react";

export type MotionProps = {
  animate?: Record<string, unknown>;
  exit?: Record<string, unknown>;
  initial?: Record<string, unknown>;
  transition?: { duration?: number; delay?: number; [key: string]: unknown };
  onAnimationComplete?: () => void;
  layout?: boolean;
  mode?: string;
  variants?: Record<string, unknown>;
  whileHover?: Record<string, unknown>;
  whileTap?: Record<string, unknown>;
};

function transformFrom(value?: Record<string, unknown>): string | undefined {
  if (!value) return undefined;
  const parts: string[] = [];
  if (typeof value.x === "number") parts.push(`translateX(${value.x}px)`);
  if (typeof value.y === "number") parts.push(`translateY(${value.y}px)`);
  if (typeof value.scale === "number") parts.push(`scale(${value.scale})`);
  return parts.length ? parts.join(" ") : undefined;
}

function styleFromMotionProps(props: MotionProps): CSSProperties {
  const target = props.animate ?? props.initial;
  if (!target) return {};
  return {
    opacity: typeof target.opacity === "number" ? target.opacity : undefined,
    filter: typeof target.filter === "string" ? target.filter : undefined,
    transform: transformFrom(target),
    transition: "opacity 180ms ease, transform 180ms ease, filter 180ms ease",
  };
}

function stripMotionProps(props: MotionProps) {
  const {
    animate,
    exit,
    initial,
    layout,
    mode,
    transition,
    variants,
    whileHover,
    whileTap,
    onAnimationComplete,
    ...rest
  } = props as MotionProps & Record<string, unknown>;
  void animate;
  void exit;
  void initial;
  void layout;
  void mode;
  void transition;
  void variants;
  void whileHover;
  void whileTap;
  void onAnimationComplete;
  return rest;
}

function createMotionComponent(tag: ElementType | string) {
  return forwardRef<
    HTMLElement,
    MotionProps & { className?: string; style?: CSSProperties }
  >(function MotionShimComponent(props, ref) {
    const cleanProps = stripMotionProps(props);
    const duration =
      typeof props.transition?.duration === "number"
        ? props.transition.duration
        : 0.18;
    const delay =
      typeof props.transition?.delay === "number" ? props.transition.delay : 0;

    useEffect(() => {
      if (!props.onAnimationComplete) return;
      const complete = props.onAnimationComplete;
      const timeout = window.setTimeout(
        complete,
        Math.max(0, (duration + delay) * 1000),
      );
      return () => window.clearTimeout(timeout);
    }, [delay, duration, props.onAnimationComplete]);

    return createElement(tag, {
      ...cleanProps,
      ref,
      style: {
        ...styleFromMotionProps(props),
        ...(props.style && typeof props.style === "object"
          ? (props.style as CSSProperties)
          : {}),
      },
    });
  });
}

export const AnimatePresence = ({
  children,
}: {
  children?: ReactNode;
  mode?: string;
}) => <Fragment>{children}</Fragment>;

export function useInView(
  ref: React.RefObject<Element | null>,
  options?: { once?: boolean; amount?: number; margin?: string },
) {
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    const element = ref.current;
    if (!element || typeof IntersectionObserver === "undefined") {
      setVisible(true);
      return;
    }
    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry?.isIntersecting) {
          setVisible(true);
          if (options?.once ?? true) observer.disconnect();
        } else if (!options?.once) {
          setVisible(false);
        }
      },
      {
        rootMargin: options?.margin,
        threshold: options?.amount ?? 0,
      },
    );
    observer.observe(element);
    return () => observer.disconnect();
  }, [options?.amount, options?.margin, options?.once, ref]);

  return visible;
}

type MotionValue = {
  get: () => number;
  set: (next: number) => void;
  on: (event: "change", listener: (latest: number) => void) => () => void;
};

export function useMotionValue(initial: number): MotionValue {
  const value = useRef(initial);
  const listeners = useRef(new Set<(latest: number) => void>());

  return {
    get: () => value.current,
    set: (next: number) => {
      value.current = next;
      for (const listener of listeners.current) listener(next);
    },
    on: (_event, listener) => {
      listeners.current.add(listener);
      return () => listeners.current.delete(listener);
    },
  };
}

export function useSpring(value: MotionValue, _options?: unknown): MotionValue {
  return value;
}

type MotionComponent = ComponentType<
  MotionProps & { className?: string; style?: CSSProperties } & Record<
      string,
      unknown
    >
>;

type MotionFactory = {
  create: (
    component: ElementType | string,
    _options?: unknown,
  ) => MotionComponent;
  div: MotionComponent;
  button: MotionComponent;
  h1: MotionComponent;
  span: MotionComponent;
} & Record<string, MotionComponent>;

export const motion = new Proxy(
  {
    create: (component: ElementType) => createMotionComponent(component),
    div: createMotionComponent("div"),
    button: createMotionComponent("button"),
    h1: createMotionComponent("h1"),
    span: createMotionComponent("span"),
  } as unknown as MotionFactory,
  {
    get(target, prop) {
      if (prop in target) return target[prop as keyof MotionFactory];
      return createMotionComponent(prop as string);
    },
  },
) as MotionFactory;
