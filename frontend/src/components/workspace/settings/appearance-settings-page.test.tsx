import { describe, expect, it } from "vitest";
import { fireEvent, screen } from "@testing-library/react";

import { renderWithProviders } from "@/test/harness";
import { SUPPORTED_LOCALES } from "@/core/i18n";
import { enUS, jaJP, koKR, zhCN, type Translations } from "@/core/i18n/locales";

import AppearanceSettingsPage from "./appearance-settings-page";

const TRANSLATIONS_BY_LOCALE: Record<
  (typeof SUPPORTED_LOCALES)[number],
  Translations
> = {
  "en-US": enUS,
  "zh-CN": zhCN,
  "ja-JP": jaJP,
  "ko-KR": koKR,
};

describe("AppearanceSettingsPage · language selector", () => {
  it("offers every supported locale", async () => {
    renderWithProviders(<AppearanceSettingsPage />);

    const trigger = screen.getByLabelText(
      enUS.settings.appearance.languageTitle,
    );
    fireEvent.pointerDown(trigger, { button: 0, ctrlKey: false });
    fireEvent.click(trigger);

    for (const locale of SUPPORTED_LOCALES) {
      expect(
        await screen.findByRole("option", {
          name: TRANSLATIONS_BY_LOCALE[locale].locale.localName,
        }),
      ).toBeInTheDocument();
    }
  });

  it("hydrates Japanese locale in the shared test harness", () => {
    renderWithProviders(<AppearanceSettingsPage />, { locale: "ja-JP" });

    expect(
      screen.getByText(jaJP.settings.appearance.themeTitle),
    ).toBeInTheDocument();
  });

  it("hydrates Korean locale in the shared test harness", () => {
    renderWithProviders(<AppearanceSettingsPage />, { locale: "ko-KR" });

    expect(
      screen.getByText(koKR.settings.appearance.themeTitle),
    ).toBeInTheDocument();
  });

  it("exposes selected themes without mixing conversation controls", () => {
    renderWithProviders(<AppearanceSettingsPage />, { locale: "zh-CN" });

    expect(screen.getByRole("button", { name: /跟随系统/ })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(
      screen.queryByLabelText(zhCN.settings.appearance.chatFontSizeTitle),
    ).not.toBeInTheDocument();
  });

  it("does not leak English corner-style names into Chinese copy", () => {
    renderWithProviders(<AppearanceSettingsPage />, { locale: "zh-CN" });

    const description = screen.getByText(
      zhCN.settings.appearance.cornerRadiusDescription,
    );
    expect(description).toHaveTextContent("锐利");
    expect(description).toHaveTextContent("胶囊");
    expect(description).not.toHaveTextContent(/Crisp|Pill/);
  });
});
