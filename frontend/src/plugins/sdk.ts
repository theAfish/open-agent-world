import type { ComponentType } from "react";
import type { NodeSurfaceLevel, NodeTypeCatalogItem, WorldCard } from "../types/world";
export type { NodePresentation, NodeSurfaceLevel } from "../types/world";
export { SchemaFields } from "./SchemaFields";
export { t, useLocale } from "../i18n";
export { useNestedFlowGestures } from "../canvas/useNestedFlowGestures";
export { useFileViewer } from "../state/openFiles";
export type { FileReference, OpenedFile } from "../state/openFiles";
export { WorkspaceSection, useWorkspaceSections } from "../workspace/WorkspaceSection";
export type { WorkspaceSectionProps } from "../workspace/WorkspaceSection";

export type PluginSlot = "preview" | "body" | "settings" | "workspace";

/** A document mutation published by the authoritative backend. */
export interface PluginDocumentChange {
  nodeId: string;
  revision: number;
  actorId?: string;
  runId?: string;
}

export interface PluginViewProps {
  card: WorldCard;
  definition: NodeTypeCatalogItem;
  level: NodeSurfaceLevel;
  host: {
    updateConfig(patch: Record<string, unknown>): Promise<void>;
    getAgentInfo(): Promise<{ session_id: string; details?: Record<string, unknown> }>;
    documentAction(action: string, arguments_: Record<string, unknown>, expectedRevision?: number): Promise<{ value: unknown; revision: number }>;
    resourceAction(action: string, arguments_: Record<string, unknown>, confirm?: boolean): Promise<Record<string, unknown>>;
    listCards(traits?: string[]): Promise<WorldCard[]>;
    readDocument(nodeId?: string): Promise<{ value: unknown; revision: number }>;
    transform(operation: string, request: Record<string, unknown>): Promise<Record<string, unknown>>;
    documentDownloadUrl(name: string): string;
    /** Select a card and open its normal OAW workspace surface. */
    openWorkspace(nodeId: string): void;
    /** Start an Agent through the ordinary OAW run lifecycle. */
    runAgent(nodeId: string, prompt: string): Promise<void>;
    /** Observe future node-document mutations received by the OAW event stream. */
    onDocumentChange(listener: (change: PluginDocumentChange) => void): () => void;
    readFile(reference: import("../state/openFiles").FileReference, signal?: AbortSignal): Promise<{ name: string; size_bytes: number; data: string }>;
    openFile(reference: import("../state/openFiles").FileReference, name: string): void;
    clearOpenedFile(): void;
  };
}
export interface FrontendPlugin {
  apiVersion: 1;
  views: Record<string, ComponentType<PluginViewProps>>;
}
