/**
 * Frontend-only registry for explicitly opt-in plugin viewport captures.
 *
 * The WebSocket observer owns delivery to the backend. Plugins only register
 * an active renderer for their own card, so a plugin cannot send screenshots
 * opportunistically or capture another card's DOM.
 */
export interface PluginVisualCaptureRequest {
  nodeId: string;
  captureKind: string;
  documentRevision: number;
  maxImageDimension: number;
  captureOptions?: Record<string, unknown>;
}

export interface PluginVisualCaptureResult {
  dataBase64: string;
  metadata?: Record<string, unknown>;
}

export type PluginVisualCapture = (request: PluginVisualCaptureRequest) => Promise<PluginVisualCaptureResult>;

const captures = new Map<string, Map<string, PluginVisualCapture>>();

export function registerPluginVisualCapture(nodeId: string, captureKind: string, capture: PluginVisualCapture): () => void {
  let nodeCaptures = captures.get(nodeId);
  if (!nodeCaptures) { nodeCaptures = new Map(); captures.set(nodeId, nodeCaptures); }
  nodeCaptures.set(captureKind, capture);
  return () => {
    const current = captures.get(nodeId);
    if (!current || current.get(captureKind) !== capture) return;
    current.delete(captureKind);
    if (!current.size) captures.delete(nodeId);
  };
}

export async function capturePluginVisual(request: PluginVisualCaptureRequest): Promise<PluginVisualCaptureResult> {
  const capture = captures.get(request.nodeId)?.get(request.captureKind);
  if (!capture) throw new Error("Open the requested plugin workspace before observing it.");
  return capture(request);
}
