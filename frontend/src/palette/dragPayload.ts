import type { CardType } from "../types/world";

export const PALETTE_DRAG_MIME = "application/vnd.open-agent-world.palette-item+json";
export const LEGACY_NODE_DRAG_MIME = "application/open-agent-card";

export type PaletteDragPayload =
  | { version: 1; kind: "node"; type: CardType }
  | { version: 1; kind: "legion"; id: string; revision: number };

export function serializePaletteDrag(payload: PaletteDragPayload): string {
  return JSON.stringify(payload);
}

export function parsePaletteDrag(value: string): PaletteDragPayload | undefined {
  if (!value) return undefined;
  try {
    const candidate: unknown = JSON.parse(value);
    if (!candidate || typeof candidate !== "object") return undefined;
    const record = candidate as Record<string, unknown>;
    if (record.version !== 1) return undefined;
    if (record.kind === "node" && typeof record.type === "string" && record.type.length > 0) {
      return { version: 1, kind: "node", type: record.type };
    }
    if (
      record.kind === "legion"
      && typeof record.id === "string"
      && record.id.length > 0
      && typeof record.revision === "number"
      && Number.isFinite(record.revision)
    ) {
      return { version: 1, kind: "legion", id: record.id, revision: record.revision };
    }
  } catch {
    // Invalid external drag data is ignored at this trust boundary.
  }
  return undefined;
}

export function writePaletteDrag(dataTransfer: DataTransfer, payload: PaletteDragPayload): void {
  dataTransfer.setData(PALETTE_DRAG_MIME, serializePaletteDrag(payload));
  if (payload.kind === "node") {
    dataTransfer.setData(LEGACY_NODE_DRAG_MIME, payload.type);
  }
  dataTransfer.effectAllowed = "copyMove";
}

export function readPaletteDrag(dataTransfer: DataTransfer): PaletteDragPayload | undefined {
  const current = parsePaletteDrag(dataTransfer.getData(PALETTE_DRAG_MIME));
  if (current) return current;
  const legacyType = dataTransfer.getData(LEGACY_NODE_DRAG_MIME);
  return legacyType ? { version: 1, kind: "node", type: legacyType } : undefined;
}

export function hasPaletteDrag(dataTransfer: DataTransfer): boolean {
  return dataTransfer.types.includes(PALETTE_DRAG_MIME)
    || dataTransfer.types.includes(LEGACY_NODE_DRAG_MIME);
}
