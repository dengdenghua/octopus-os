import path from "node:path";
import { fileURLToPath } from "node:url";
import { writeFile } from "node:fs/promises";

import { Ico, IcoImage } from "@fiahfy/ico";
import sharp from "sharp";

const frontendDir = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "..",
);
const faviconSource = path.join(frontendDir, "public", "favicon.svg");
const appIconSource = path.join(
  frontendDir,
  "public",
  "images",
  "echo-app-icon.svg",
);
const target = path.resolve(
  frontendDir,
  "..",
  "packaging",
  "desktop",
  "icon.png",
);
const faviconTargets = [
  path.join(frontendDir, "public", "favicon.ico"),
  path.resolve(
    frontendDir,
    "..",
    "deploy",
    "appliance",
    "agent-webui",
    "favicon.ico",
  ),
];

await sharp(appIconSource).resize(512, 512).png().toFile(target);

const ico = new Ico();
for (const size of [16, 24, 32, 48, 64, 128, 256]) {
  const png = await sharp(faviconSource).resize(size, size).png().toBuffer();
  ico.append(IcoImage.fromPNG(png));
}
await Promise.all(faviconTargets.map((file) => writeFile(file, ico.data)));

console.log(`Generated ${target} and ${faviconTargets.join(", ")}`);
