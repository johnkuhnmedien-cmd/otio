import { readFile, mkdir } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { bundle } from "@remotion/bundler";
import { renderMedia, selectComposition } from "@remotion/renderer";
import { resolveSharedBundle } from "./bundle-cache.mjs";

const rendererRoot = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "..",
);

function argument(name) {
  const index = process.argv.indexOf(name);
  if (index === -1 || !process.argv[index + 1]) {
    throw new Error(`Fehlendes Argument: ${name}`);
  }
  return process.argv[index + 1];
}

const inputPath = path.resolve(argument("--input"));
const outputPath = path.resolve(argument("--output"));
const inputProps = JSON.parse(await readFile(inputPath, "utf8"));
await mkdir(path.dirname(outputPath), { recursive: true });

let lastReportedPercent = -1;

function reportProgress(progress) {
  const percent = Math.max(0, Math.min(100, Math.floor(progress * 100)));
  if (percent > lastReportedPercent) {
    lastReportedPercent = percent;
    process.stdout.write(`OTIO_MAP_RENDER_PROGRESS=${percent / 100}\n`);
  }
}

const serveUrl = await resolveSharedBundle({
  rendererRoot,
  bundleFn: bundle,
  onProgress: (progress) => reportProgress(0.05 + progress * 0.07),
});
reportProgress(0.12);
const composition = await selectComposition({
  serveUrl,
  id: "MapTransition",
  inputProps,
});

await renderMedia({
  composition,
  serveUrl,
  codec: "h264",
  audioCodec: null,
  crf: 17,
  enforceAudioTrack: false,
  imageFormat: "png",
  concurrency: 1,
  inputProps,
  muted: true,
  onProgress: ({ progress }) => {
    reportProgress(0.12 + progress * 0.88);
  },
  outputLocation: outputPath,
  overwrite: true,
  chromiumOptions: {
    disableWebSecurity: false,
  },
});
