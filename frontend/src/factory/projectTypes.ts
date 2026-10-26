import type { FaceDesign, FunctionDesign } from './types';
import type { PluginSlot } from '../plugins/sdk';

export interface CardInterface {
  face: FaceDesign;
  function: FunctionDesign;
  action: string;
  result_key: string;
  surfaces: PluginSlot[];
}
export interface ProjectCard {
  id: string; name?: string; label: string; kind: 'declarative' | 'python';
  design_path: string | null; design: CardInterface | null; source_path?: string;
  frontend?: Partial<Record<PluginSlot, string>>;
  actions?: { id: string; label?: string }[];
}
export interface ProjectFile { path: string; size_bytes: number; sha256: string; editable: boolean }
export interface ProjectTextFile { path: string; content: string; sha256: string }
export interface ProjectDiagnostic { severity?: string; path?: string; line?: number; column?: number; message: string }
export interface PackProject {
  id: string; path: string; name: string; distribution_id: string; version: string; revision: string;
  files: ProjectFile[]; cards: ProjectCard[]; spec: Record<string, unknown>; diagnostics: ProjectDiagnostic[];
  relationships?: { id: string; label?: string; source_types?: string[]; target_types?: string[]; source_path?: string }[];
}
export interface ProjectCheck { ok: boolean; diagnostics: ProjectDiagnostic[]; level: string }
export interface ProjectList { projects: Pick<PackProject, 'id' | 'path' | 'name' | 'distribution_id'>[]; default_directory: string }
