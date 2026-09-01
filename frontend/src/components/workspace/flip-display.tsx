import { AnimatePresence, motion } from "motion/react";
import { useEffect, useState } from "react";

import { cn } from "@/lib/utils";

function usePrefersReducedMotion(): boolean {
  const [reduced, setReduced] = useState(
    () =>
      typeof window !== "undefined" &&
      typeof window.matchMedia === "function" &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches,
  );
  useEffect(() => {
    if (
      typeof window === "undefined" ||
      typeof window.matchMedia !== "function"
    ) {
      return;
    }
    const mql = window.matchMedia("(prefers-reduced-motion: reduce)");
    const handleChange = () => setReduced(mql.matches);
    mql.addEventListener("change", handleChange);
    return () => mql.removeEventListener("change", handleChange);
  }, []);
  return reduced;
}

/**
 * Single-line value swap (e.g. the streaming status chip).
 *
 * ``popLayout`` lets the outgoing frame exit out of flow instead of waiting
 * for its exit animation ("wait" serialized 2×250ms per change — during
 * rapid tool events the chip lagged ~500ms behind reality). The shortened
 * 140ms transition keeps density readable. Reduced-motion users get an
 * instant swap with no vertical displacement.
 */
export function FlipDisplay({
  uniqueKey,
  children,
  className,
}: {
  uniqueKey: string;
  children: React.ReactNode;
  className?: string;
}) {
  const reducedMotion = usePrefersReducedMotion();

  if (reducedMotion) {
    return <div className={cn("relative", className)}>{children}</div>;
  }

  return (
    <div className={cn("relative overflow-hidden", className)}>
      <AnimatePresence mode="popLayout">
        <motion.div
          key={uniqueKey}
          initial={{ y: 6, opacity: 0 }}
          animate={{ y: 0, opacity: 1 }}
          exit={{ y: -6, opacity: 0 }}
          transition={{ duration: 0.14, ease: [0.4, 0, 0.2, 1] }}
        >
          {children}
        </motion.div>
      </AnimatePresence>
    </div>
  );
}
