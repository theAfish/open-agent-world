import { afterEach, expect, it } from "vitest";
import { capturePluginVisual, registerPluginVisualCapture } from "./visualCapture";

afterEach(() => { /* Each test unregisters its own capture. */ });

it("routes a plugin capture only to the registered card and capture kind", async () => {
  const unregister = registerPluginVisualCapture("structure-a", "atomsculptor.structure-viewport", async request => ({
    dataBase64: "png-data", metadata: { revision: request.documentRevision },
  }));
  await expect(capturePluginVisual({
    nodeId: "structure-a", captureKind: "atomsculptor.structure-viewport",
    documentRevision: 7, maxImageDimension: 1280,
  })).resolves.toEqual({ dataBase64: "png-data", metadata: { revision: 7 } });
  unregister();
  await expect(capturePluginVisual({
    nodeId: "structure-a", captureKind: "atomsculptor.structure-viewport",
    documentRevision: 7, maxImageDimension: 1280,
  })).rejects.toThrow("Open the requested plugin workspace");
});
