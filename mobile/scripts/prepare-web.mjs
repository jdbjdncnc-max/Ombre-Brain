import { cp, readFile, rm, stat, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const mobileRoot = path.resolve(scriptDir, "..");
const repositoryRoot = path.resolve(mobileRoot, "..");
const sourceDir = path.join(repositoryRoot, "companion_frontend");
const outputDir = path.join(mobileRoot, "www");

await requireFrontendSource(sourceDir);
await rm(outputDir, { recursive: true, force: true });
await cp(sourceDir, outputDir, {
  recursive: true,
  filter(source) {
    return ![".DS_Store", "Thumbs.db"].includes(path.basename(source));
  }
});

const metadata = {
  schema: "ombre.frontend-bundle.v1",
  source: "companion_frontend",
  purpose: "bundled-offline-fallback",
  minimumNativeBridgeVersion: 1
};

await writeFile(
  path.join(outputDir, "mobile-build.json"),
  `${JSON.stringify(metadata, null, 2)}\n`,
  "utf8"
);

console.log(`Prepared Android fallback frontend: ${outputDir}`);

async function requireFrontendSource(directory) {
  const indexPath = path.join(directory, "index.html");
  const index = await readFile(indexPath, "utf8");
  const source = await stat(directory);

  if (!source.isDirectory() || !/<head(?:\s|>)/i.test(index)) {
    throw new Error("companion_frontend is not a valid Capacitor web source");
  }
}
