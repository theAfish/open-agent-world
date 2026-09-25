import type { ComponentType } from "react";
import type { NodeSurfaceLevel, NodeTypeCatalogItem, WorldCard } from "../types/world";
export type { NodePresentation, NodeSurfaceLevel, PluginStateSpec } from "../types/world";
export { SchemaFields } from "./SchemaFields";
export { t, useLocale } from "../i18n";
export { useNestedFlowGestures } from "../canvas/useNestedFlowGestures";
export { useFileViewer } from "../state/openFiles";
export type { FileReference, OpenedFile } from "../state/openFiles";
export { WorkspaceSection, useWorkspaceSections } from "../workspace/WorkspaceSection";
export type { WorkspaceSectionProps } from "../workspace/WorkspaceSection";

export interface CardStateStore {
  get(): Promise<{ value: Record<string, unknown>; revision: number }>;
  set(value: Record<string, unknown>, expectedRevision?: number): Promise<{ value: Record<string, unknown>; revision: number }>;
  update(patch: Record<string, unknown>, expectedRevision?: number): Promise<{ value: Record<string, unknown>; revision: number }>;
  delete(expectedRevision?: number): Promise<{ value: Record<string, unknown>; revision: number }>;
}

export type PluginSlot = "preview" | "body" | "settings" | "workspace";
export interface XrdMultiphaseOptions {
  restart?: boolean;
  agent_node_id?: string;
  optimizer_label?: string;
  source_match_run_id?: string;
  candidate_ids?: string[];
  budget?: number;
  max_phases?: number;
  evaluate_baseline?: boolean;
}
export interface XrdMultiphaseStructure {
  candidate_id: string;
  label?: string;
  cif?: { filename: string; source_base64: string; sha256?: string };
  source_kind: 'profile_cell_fit' | 'pywpem_cell_fit' | 'input';
  error?: string;
}
export interface XrdMultiphaseTrial {
  trial_id: string;
  iteration: number;
  candidate_ids: string[];
  labels?: string[];
  status: string;
  score?: number;
  metrics?: { rwp_percent?: number; rp_percent?: number };
  validation?: {
    metrics?: { rwp_percent?: number; rp_percent?: number };
    method?: string;
    training_count?: number;
    holdout_count?: number;
    independent_experiment?: boolean;
  };
  fit?: {
    screening_method?: string;
    converged?: boolean;
    zero_shift_deg?: number;
    fwhm_deg?: number;
    lorentz_fraction?: number;
    parameter_bounds?: Record<string, number[]>;
    atomic_fractional_coordinates_fixed?: boolean;
    reflection_relative_intensities_fixed?: boolean;
  };
  phase_contributions?: {
    candidate_id: string;
    label?: string;
    formula?: string;
    profile_area_fraction?: number;
    isotropic_cell_scale?: number;
    cell_input?: number[];
    cell_fitted?: number[];
  }[];
  reason?: string;
  error?: string;
  converged?: boolean;
  structures?: XrdMultiphaseStructure[];
  plot?: {
    observed: number[][];
    calculated: number[][];
    contributions?: { candidate_id: string; label: string; points: number[][] }[];
  };
}
export interface XrdMultiphaseState {
  next_options?: XrdMultiphaseOptions;
  review_recommendations?: {status: string; combinations: string[][]; error?: string; model?: string};
  timing?: Record<string, {started_at_ms?: number; finished_at_ms?: number; running?: boolean}>;
  pywpem_review?: {
    selected_combinations?: string[][];
    progress?: {stage: string; iteration?: number; candidate_ids?: string[]};
    status: string; error?: string; scope?: string;
    reviews?: { full: { status: string; candidate_ids: string[]; labels?: string[]; error?: string;
      metrics?: { rwp_percent?: number; rp_percent?: number }; converged?: boolean;
      iteration?: number; plot?: XrdMultiphaseTrial['plot']; structures?: XrdMultiphaseStructure[] };
      removals: { omitted_candidate_id: string; status: string; delta_rwp?: number | null; error?: string }[] }[];
    live?: { stage: 'pywpem'; iteration: number; candidate_ids: string[]; labels?: string[];
      plot?: XrdMultiphaseTrial['plot']; structures?: XrdMultiphaseStructure[];
      metrics?: { rwp_percent?: number; rp_percent?: number }; status: string; trial_id: string };
  };
  agent_node_id?: string;
  controller?: { agent_node_id: string; name: string; model?: string };
  available_agents?: { agent_node_id: string; name: string; model?: string }[];
  /** Persisted with the scientific run; never inferred from the next selected Agent. */
  optimizer_label?: string;
  run_id?: string;
  protocol_version?: string;
  cloud_request_count?: number;
  cloud_request_limit?: number;
  draft_candidate_ids?: string[];
  source_match_run_id?: string;
  status: string;
  llm_status?: string;
  resumable?: boolean;
  progress?: { completed: number; total: number; stage: string };
  trials?: XrdMultiphaseTrial[];
  incumbent?: XrdMultiphaseTrial | null;
  best_multiphase?: XrdMultiphaseTrial | null;
  baseline?: { status: string; trials?: XrdMultiphaseTrial[]; incumbent?: XrdMultiphaseTrial | null; best_multiphase?: XrdMultiphaseTrial | null; evaluations?: number; error?: string };
  pool?: { candidate_id: string; label?: string; formula?: string }[];
  excluded_candidates?: { candidate_id: string; input_error?: string }[];
  budget_per_optimizer?: number;
  error?: string;
  stop_reason?: string;
  objective_description?: string;
  claim_scope?: string;
  optimizer?: string;
  model?: string;
}
export interface PluginViewProps {
  card: WorldCard;
  definition: NodeTypeCatalogItem;
  level: NodeSurfaceLevel;
  host: {
    /** Absent for state.mode=none. The host owns namespace and lifecycle. */
    state?: CardStateStore;
    /** Present only when the developer permits a choice; null restores its default. */
    setDataPersistence?: (value: "shared" | "session" | null) => Promise<void>;
    /** Present only in a deployed Workspace. Reuse the view and hide engineering controls. */
    deployment?: import('../workspace/WorkspaceAccess').PluginDeploymentAccess;
    getInputs?(nodeId?:string): Promise<{id:string;name:string;kind:string;ready:boolean;detail:string}[]>;
    ensureXrdInput?(kind:'pattern'|'library'): Promise<string>;
    startXrdWorkflow?(file:File): Promise<void>;
    runAnalysis?(): Promise<void>;
    stopAnalysis?(): Promise<void>;
    saveMultiphaseOptions?(options: XrdMultiphaseOptions): Promise<unknown>;
    reviewMultiphase?(runId: string, combinations: string[][]): Promise<unknown>;
    startMultiphase?(options: XrdMultiphaseOptions): Promise<unknown>;
    getMultiphaseFrame?(selection: {run_id:string;lane:string;trial_id?:string;review_index?:number}): Promise<{plot?:XrdMultiphaseTrial['plot'];structures?:XrdMultiphaseTrial['structures']}>;
    getMultiphase?(): Promise<XrdMultiphaseState>;
    stopMultiphase?(): Promise<unknown>;
    openResultsConversation?(): Promise<unknown>;
    updateConfig(patch: Record<string, unknown>): Promise<void>;
    getAgentInfo(nodeId?: string): Promise<{ session_id: string; details?: Record<string, unknown> }>;
    openLinkedCanvas?(type: string, name: string): Promise<void>;
    documentAction(action: string, arguments_: Record<string, unknown>, expectedRevision?: number, nodeId?:string): Promise<{ value: unknown; revision: number }>;
    delegationAction(action: 'collect' | 'wait' | 'stop', arguments_: Record<string, unknown>): Promise<Record<string, unknown>>;
    resourceAction(action: string, arguments_: Record<string, unknown>, confirm?: boolean): Promise<Record<string, unknown>>;
    listCards(traits?: string[]): Promise<WorldCard[]>;
    readDocument(nodeId?: string): Promise<{ value: unknown; revision: number }>;
    transform(operation: string, request: Record<string, unknown>): Promise<Record<string, unknown>>;
    documentDownloadUrl(name: string, nodeId?:string): string;
    readFile(reference: import("../state/openFiles").FileReference, signal?: AbortSignal): Promise<{ name: string; size_bytes: number; data: string }>;
    openFile(reference: import("../state/openFiles").FileReference, name: string): void;
    clearOpenedFile(): void;
    openInputNode?(type: string, name: string, source: { filename: string; source_base64: string }, relationship?: string): Promise<void>;
  };
}
export interface FrontendPlugin {
  apiVersion: 1;
  views: Record<string, ComponentType<PluginViewProps>>;
}
export {ArrowLeft,ArrowRight,Play,Pause,RotateCcw,Square,Link2,Settings2} from "lucide-react";
