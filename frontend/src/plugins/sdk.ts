import type { ComponentType } from "react";
import type { NodeTypeCatalogItem, WorldCard } from "../types/world";
export { SchemaFields } from "./SchemaFields";
export { t, useLocale } from "../i18n";
export { useNestedFlowGestures } from "../canvas/useNestedFlowGestures";
export { useFileViewer } from "../state/openFiles";
export type { FileReference, OpenedFile } from "../state/openFiles";

export type PluginSlot = "preview" | "body" | "settings" | "workspace";
export interface PluginViewProps {
  card: WorldCard;
  definition: NodeTypeCatalogItem;
  level: "node" | "preview" | "inspector" | "workspace";
  host: {
    updateConfig(patch: Record<string, unknown>): Promise<void>;
    getAgentInfo(): Promise<{ session_id: string; details?: Record<string, unknown> }>;
    documentAction(action: string, arguments_: Record<string, unknown>, expectedRevision?: number): Promise<{ value: unknown; revision: number }>;
    listCards(traits?: string[]): Promise<WorldCard[]>;
    readDocument(nodeId?: string): Promise<{ value: unknown; revision: number }>;
    transform(operation: string, request: Record<string, unknown>): Promise<Record<string, unknown>>;
    documentDownloadUrl(name: string): string;
    readFile(reference: import("../state/openFiles").FileReference, signal?: AbortSignal): Promise<{ name: string; size_bytes: number; data: string }>;
    openFile(reference: import("../state/openFiles").FileReference, name: string): void;
    clearOpenedFile(): void;
  };
}
export interface FrontendPlugin {
  apiVersion: 1;
  views: Record<string, ComponentType<PluginViewProps>>;
}
