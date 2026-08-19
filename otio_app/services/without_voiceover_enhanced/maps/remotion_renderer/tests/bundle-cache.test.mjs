import assert from "node:assert/strict";
import { mkdtemp, mkdir, readFile, rm, writeFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { resolveSharedBundle, rendererFingerprint } from "../scripts/bundle-cache.mjs";

async function fixture() {
  const root = await mkdtemp(
    path.join(process.cwd(), ".otio-map-cache-test-"),
  );
  await mkdir(path.join(root, "src"), { recursive: true });
  await mkdir(path.join(root, "public"), { recursive: true });
  await writeFile(path.join(root, "src", "index.ts"), "registerRoot(() => null);\n");
  await writeFile(path.join(root, "public", "map.json"), "{}\n");
  await writeFile(path.join(root, "package.json"), '{"name":"fixture"}\n');
  await writeFile(path.join(root, "package-lock.json"), '{"lockfileVersion":3}\n');
  return root;
}

test("parallel renders prepare one shared Remotion bundle", async (context) => {
  const rendererRoot = await fixture();
  context.after(() => rm(rendererRoot, { recursive: true, force: true }));
  let bundleCalls = 0;
  const bundleFn = async ({ outDir, onProgress }) => {
    bundleCalls += 1;
    onProgress(0.5);
    await new Promise((resolve) => setTimeout(resolve, 100));
    await writeFile(path.join(outDir, "index.html"), "ready");
    onProgress(1);
    return outDir;
  };

  const results = await Promise.all(
    Array.from({ length: 4 }, () =>
      resolveSharedBundle({ rendererRoot, bundleFn }),
    ),
  );

  assert.equal(bundleCalls, 1);
  assert.equal(new Set(results).size, 1);
  assert.equal(await readFile(path.join(results[0], "index.html"), "utf8"), "ready");
});

test("stale lock is repaired and bundling continues", async (context) => {
  const rendererRoot = await fixture();
  context.after(() => rm(rendererRoot, { recursive: true, force: true }));
  const fingerprint = await rendererFingerprint(rendererRoot);
  const lockDirectory = path.join(
    rendererRoot,
    ".cache",
    "map-bundle-locks",
    fingerprint,
  );
  await mkdir(lockDirectory, { recursive: true });
  await writeFile(
    path.join(lockDirectory, "owner.json"),
    JSON.stringify({ pid: 999999, createdAt: new Date().toISOString() }),
  );
  let bundleCalls = 0;
  const bundleFn = async ({ outDir }) => {
    bundleCalls += 1;
    await writeFile(path.join(outDir, "index.html"), "repaired");
    return outDir;
  };

  const result = await resolveSharedBundle({ rendererRoot, bundleFn });

  assert.equal(bundleCalls, 1);
  assert.equal(
    await readFile(path.join(result, "index.html"), "utf8"),
    "repaired",
  );
});

test("source changes create a fresh shared bundle", async (context) => {
  const rendererRoot = await fixture();
  context.after(() => rm(rendererRoot, { recursive: true, force: true }));
  let bundleCalls = 0;
  const bundleFn = async ({ outDir }) => {
    bundleCalls += 1;
    await writeFile(path.join(outDir, "index.html"), String(bundleCalls));
    return outDir;
  };

  const first = await resolveSharedBundle({ rendererRoot, bundleFn });
  const reused = await resolveSharedBundle({ rendererRoot, bundleFn });
  await writeFile(
    path.join(rendererRoot, "src", "index.ts"),
    "registerRoot(() => 'changed');\n",
  );
  const changed = await resolveSharedBundle({ rendererRoot, bundleFn });

  assert.equal(bundleCalls, 2);
  assert.equal(first, reused);
  assert.notEqual(first, changed);
});
