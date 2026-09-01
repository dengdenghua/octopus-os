import { useEffect } from "react";
import { ThemeProvider as NextThemesProvider } from "next-themes";

function SystemLiquidGlassTheme() {
  useEffect(() => {
    document.documentElement.classList.add("apple");
    return () => document.documentElement.classList.remove("apple");
  }, []);

  return null;
}

export function ThemeProvider({
  children,
}: React.ComponentProps<typeof NextThemesProvider>) {
  return (
    <NextThemesProvider
      attribute="class"
      defaultTheme="system"
      enableSystem
      storageKey="echo-system-theme"
      themes={["light", "dark"]}
    >
      <SystemLiquidGlassTheme />
      {children}
    </NextThemesProvider>
  );
}
