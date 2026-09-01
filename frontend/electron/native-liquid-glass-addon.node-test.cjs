const path = require("node:path");

const { app } = require("electron");

app
  .whenReady()
  .then(() => {
    const addon = require(
      path.join(
        __dirname,
        "..",
        "native",
        "echo-liquid-glass",
        "build",
        "Release",
        "echo_liquid_glass.node",
      ),
    );
    if (!addon.hasLiquidGlass()) {
      throw new Error("NSGlassEffectView is unavailable on this macOS build");
    }
    console.log("ECHO_NATIVE_LIQUID_GLASS_ADDON_OK NSGlassEffectView");
    app.quit();
  })
  .catch((error) => {
    console.error(error);
    app.exit(1);
  });
