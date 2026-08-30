import { useLocation } from "react-router-dom";
import { ThemeProvider as NextThemesProvider } from "next-themes";

const THEME_LIST = ["light", "dark", "apple"];

export function ThemeProvider({
  children,
  ...props
}: React.ComponentProps<typeof NextThemesProvider>) {
  const { pathname } = useLocation();
  return (
    <NextThemesProvider
      {...props}
      attribute="class"
      forcedTheme={pathname === "/" ? "dark" : undefined}
      themes={THEME_LIST}
      enableSystem
    >
      {children}
    </NextThemesProvider>
  );
}
