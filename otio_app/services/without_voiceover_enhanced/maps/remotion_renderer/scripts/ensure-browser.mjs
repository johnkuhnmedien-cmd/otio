import { ensureBrowser } from "@remotion/renderer";

const status = await ensureBrowser({
  chromeMode: "headless-shell",
  logLevel: "info",
});

if (status.type === "no-browser") {
  throw new Error("Remotion konnte keinen lokalen Render-Browser einrichten.");
}
