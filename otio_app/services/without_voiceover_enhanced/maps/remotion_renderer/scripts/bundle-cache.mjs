import { createHash } from "node:crypto";
import {
  mkdir,
  readFile,
  readdir,
  rm,
  stat,
  writeFile,
} from "node:fs/promises";
import path from "node:path";

const wait = (milliseconds) =>
  new Promise((resolve) => setTimeout(resolve, milliseconds));

async function exists(candidate) {
  try {
    await stat(candidate);
    return true;
  } catch (error) {
    if (error?.code === "ENOENT") {
      return false;
    }
    throw error;
  }
}

async function collectFiles(root, relative = "") {
  const directory = path.join(root, relative);
  const entries = await readdir(directory, { withFileTypes: true });
  const files = [];
  for (const entry of entries.sort((left, right) =>
    left.name.localeCompare(right.name),
  )) {
    const child = path.join(relative, entry.name);
    if (entry.isDirectory()) {
      files.push(...(await collectFiles(root, child)));
    } else if (entry.isFile()) {
      files.push(child);
    }
  }
  return files;
}

export async function rendererFingerprint(rendererRoot) {
  const digest = createHash("sha256");
  const roots = ["src", "public"];
  for (const relativeRoot of roots) {
    const absoluteRoot = path.join(rendererRoot, relativeRoot);
    if (!(await exists(absoluteRoot))) {
      continue;
    }
    for (const relativeFile of await collectFiles(absoluteRoot)) {
      digest.update(`${relativeRoot}/${relativeFile}\0`);
      digest.update(await readFile(path.join(absoluteRoot, relativeFile)));
      digest.update("\0");
    }
  }
  for (const filename of ["package.json", "package-lock.json"]) {
    const candidate = path.join(rendererRoot, filename);
    if (await exists(candidate)) {
      digest.update(`${filename}\0`);
      digest.update(await readFile(candidate));
      digest.update("\0");
    }
  }
  return digest.digest("hex");
}

function processIsAlive(processId) {
  if (!Number.isInteger(processId) || processId < 1) {
    return false;
  }
  try {
    process.kill(processId, 0);
    return true;
  } catch (error) {
    return error?.code === "EPERM";
  }
}

async function lockIsStale(lockDirectory, staleAfterMilliseconds) {
  try {
    const owner = JSON.parse(
      await readFile(path.join(lockDirectory, "owner.json"), "utf8"),
    );
    if (processIsAlive(Number(owner.pid))) {
      return false;
    }
    return true;
  } catch (error) {
    if (error?.code !== "ENOENT" && !(error instanceof SyntaxError)) {
      throw error;
    }
  }
  try {
    const lockStats = await stat(lockDirectory);
    return Date.now() - lockStats.mtimeMs > staleAfterMilliseconds;
  } catch (error) {
    if (error?.code === "ENOENT") {
      return true;
    }
    throw error;
  }
}

export async function resolveSharedBundle({
  rendererRoot,
  bundleFn,
  onProgress = () => {},
  waitTimeoutMilliseconds = 12 * 60 * 1000,
  staleAfterMilliseconds = 10 * 60 * 1000,
}) {
  const fingerprint = await rendererFingerprint(rendererRoot);
  const cacheRoot = path.join(rendererRoot, ".cache", "map-bundles");
  const bundleDirectory = path.join(cacheRoot, fingerprint);
  const readyMarker = path.join(bundleDirectory, ".ready.json");
  const lockRoot = path.join(rendererRoot, ".cache", "map-bundle-locks");
  const lockDirectory = path.join(lockRoot, fingerprint);

  await mkdir(cacheRoot, { recursive: true });
  await mkdir(lockRoot, { recursive: true });
  if (await exists(readyMarker)) {
    onProgress(1);
    return bundleDirectory;
  }

  const waitStartedAt = Date.now();
  while (true) {
    let ownsLock = false;
    try {
      await mkdir(lockDirectory);
      ownsLock = true;
      await writeFile(
        path.join(lockDirectory, "owner.json"),
        JSON.stringify({ pid: process.pid, createdAt: new Date().toISOString() }),
        "utf8",
      );
    } catch (error) {
      if (error?.code !== "EEXIST") {
        throw error;
      }
    }

    if (ownsLock) {
      try {
        if (await exists(readyMarker)) {
          onProgress(1);
          return bundleDirectory;
        }
        await rm(bundleDirectory, { recursive: true, force: true });
        await mkdir(bundleDirectory, { recursive: true });
        const serveUrl = await bundleFn({
          entryPoint: path.join(rendererRoot, "src", "index.ts"),
          publicDir: path.join(rendererRoot, "public"),
          outDir: bundleDirectory,
          enableCaching: true,
          onProgress,
        });
        await writeFile(
          readyMarker,
          JSON.stringify({ fingerprint, createdAt: new Date().toISOString() }),
          "utf8",
        );
        onProgress(1);
        return serveUrl;
      } catch (error) {
        await rm(bundleDirectory, { recursive: true, force: true });
        throw error;
      } finally {
        await rm(lockDirectory, { recursive: true, force: true });
      }
    }

    if (await exists(readyMarker)) {
      onProgress(1);
      return bundleDirectory;
    }
    if (await lockIsStale(lockDirectory, staleAfterMilliseconds)) {
      await rm(lockDirectory, { recursive: true, force: true });
      continue;
    }
    if (Date.now() - waitStartedAt > waitTimeoutMilliseconds) {
      throw new Error(
        "Die gemeinsame Vorbereitung des Kartenrenderers hat zu lange gedauert.",
      );
    }
    onProgress(0.25);
    await wait(250);
  }
}
