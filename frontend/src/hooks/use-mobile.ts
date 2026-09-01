import { useEffect, useState } from "react";

const MOBILE_BREAKPOINT = 768;
const MQL_QUERY = `(max-width: ${MOBILE_BREAKPOINT - 1}px)`;

export function useIsMobile(): boolean {
  const [isMobile, setIsMobile] = useState(
    () => typeof window !== "undefined" && window.matchMedia(MQL_QUERY).matches,
  );

  useEffect(() => {
    const mql = window.matchMedia(MQL_QUERY);
    const onChange = () => setIsMobile(mql.matches);
    mql.addEventListener("change", onChange);
    return () => mql.removeEventListener("change", onChange);
  }, []);

  return isMobile;
}
