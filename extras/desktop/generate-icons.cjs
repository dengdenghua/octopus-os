/**
 * Generate the icon files required for Electron packaging from SVG.
 * - Windows: icon.ico (256x256)
 * - macOS: icon.icns (512x512)
 * - Linux: icon.png (512x512)
 */
const fs = require("fs");
const path = require("path");
const sharp = require("sharp");

const SVG_INPUT = path.join(__dirname, "..", "..", "frontend", "public", "images", "echo.svg");
const BUILD_DIR = path.join(__dirname, "..", "build");

async function generateIcons() {
  if (!fs.existsSync(SVG_INPUT)) {
    console.error("SVG not found:", SVG_INPUT);
    process.exit(1);
  }

  if (!fs.existsSync(BUILD_DIR)) {
    fs.mkdirSync(BUILD_DIR, { recursive: true });
  }

  const svgBuffer = fs.readFileSync(SVG_INPUT);

  // Windows ICO (256x256, 48x48, 32x32, 16x16)
  const sizes = [16, 32, 48, 256];
  const pngBuffers = await Promise.all(
    sizes.map((size) =>
      sharp(svgBuffer)
        .resize(size, size, { fit: "contain", background: { r: 0, g: 0, b: 0, alpha: 0 } })
        .png()
        .toBuffer()
    )
  );

  // sharp does not support ico directly, so generate PNG assets that electron-builder can use.
  // A 256x256 PNG gives electron-builder a stable source for compatibility handling.
  const icon256 = path.join(BUILD_DIR, "icon.png");
  await sharp(svgBuffer)
    .resize(256, 256, { fit: "contain", background: { r: 0, g: 0, b: 0, alpha: 0 } })
    .png()
    .toFile(icon256);
  console.log("Generated:", icon256);

  // Generate a high-resolution version for macOS.
  const icon512 = path.join(BUILD_DIR, "icon-512.png");
  await sharp(svgBuffer)
    .resize(512, 512, { fit: "contain", background: { r: 0, g: 0, b: 0, alpha: 0 } })
    .png()
    .toFile(icon512);
  console.log("Generated:", icon512);

  // Generate the Windows ico file with @fiahfy/ico when available.
  try {
    const { Ico, IcoImage } = require("@fiahfy/ico");
    const ico = new Ico();
    for (const buffer of pngBuffers) {
      ico.append(IcoImage.fromPNG(buffer));
    }
    const icoPath = path.join(BUILD_DIR, "icon.ico");
    fs.writeFileSync(icoPath, ico.data);
    console.log("Generated:", icoPath);
  } catch (e) {
    console.warn("ICO generation skipped (install @fiahfy/ico for full support):", e.message);
    // Fallback: copy the PNG as icon.ico, which electron-builder accepts in some setups.
    fs.copyFileSync(icon256, path.join(BUILD_DIR, "icon.ico"));
    console.log("Fallback copied PNG to icon.ico");
  }

  // Generate the macOS icns file from the high-resolution PNG.
  // electron-builder can derive icns from a single PNG, so 512px is enough.
  fs.copyFileSync(icon512, path.join(BUILD_DIR, "icon.icns"));
  console.log("Generated icon.icns (from 512.png)");

  console.log("\nAll icons generated in:", BUILD_DIR);
}

generateIcons().catch((err) => {
  console.error(err);
  process.exit(1);
});
