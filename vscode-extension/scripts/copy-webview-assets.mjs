import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const root = path.resolve(__dirname, "..");

const mediaSource = path.join(root, "src", "ui", "media");
const mediaTarget = path.join(root, "dist", "ui", "media");
const vendorTarget = path.join(root, "dist", "ui", "vendor");

const vendorFiles = [
  {
    source: path.join(root, "node_modules", "marked", "lib", "marked.umd.js"),
    target: path.join(vendorTarget, "marked.umd.js"),
  },
  {
    source: path.join(root, "node_modules", "dompurify", "dist", "purify.min.js"),
    target: path.join(vendorTarget, "purify.min.js"),
  },
];

const ensureDir = (targetPath) => {
  fs.mkdirSync(targetPath, { recursive: true });
};

const copyFile = (source, target) => {
  if (!fs.existsSync(source)) {
    throw new Error(`Missing runtime asset: ${source}`);
  }
  ensureDir(path.dirname(target));
  fs.copyFileSync(source, target);
};

fs.rmSync(mediaTarget, { recursive: true, force: true });
fs.rmSync(vendorTarget, { recursive: true, force: true });

ensureDir(path.dirname(mediaTarget));
fs.cpSync(mediaSource, mediaTarget, { recursive: true });

for (const entry of vendorFiles) {
  copyFile(entry.source, entry.target);
}
