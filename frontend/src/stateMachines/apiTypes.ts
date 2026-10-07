import type { MachineEvent, StateMachine, MachineAction } from './model';

﻿export interface StateMachineEventDescriptor {
  key: string;
  label: string;
  category: string;
  outcome: string | null;
  runtime_bound: boolean;
  description?: string;
  state_selector?: boolean;
}
export interface StateMachineOperation {
  kind: string;
  operation_id?: string;
  operation_kind?: string;
  action?: string;
  tool_name: string | null;
  label?: string | null;
  target_card_id: string;
  target_name: string;
  target_type: string;
}
export interface StateMachineEventCatalog {
  events: StateMachineEventDescriptor[];
  operations: StateMachineOperation[];
  sources: StateMachineEventSource[];
}
export interface StateMachineEventSource {
  id: string;
  label: string;
  target_card_id: string | null;
  target_name: string;
  operation_id?: string;
  kind?: string;
  capability: string | null;
  default_event: string;
  events: StateMachineEventDescriptor[];
}

export interface MachinePresentation {
  coordinate_space?: 'owner';
  positions?: Record<string, Record<string, { x: number; y: number }>>;
  viewports?: Record<string, { x: number; y: number; zoom: number }>;
  expanded?: string[];
}
export type MachineDefinition = Omit<StateMachine, 'entities'> & {
  entities: (Omit<StateMachine['entities'][number], 'states'> & {
    states: { id: string; label: string }[];
  })[];
};
export interface MachineDocument {
  definition: MachineDefinition | null;
  definition_version: number;
  revision: number;
  enabled: boolean;
  active_definition_versions?: number[];
  presentation: MachinePresentation;
}
export interface MachineMember { id: string; name: string; type: string; has_definition: boolean; state_machine_editor?: boolean; has_members?: boolean }
export interface PreviewEvent extends MachineEvent { event_id: string; time_ms: number; scope_key: string }
export interface PreviewResult {
  states: Record<string, string>;
  last_rule_id?: string;
  steps: { event_id: string; states: Record<string, string>; rule_id?: string; rules: {rule_id: string; reason: string}[]; actions?: MachineAction[] }[];
}
export interface MachineRuntime {
  instances: { id: string; scope_key?: string; definition_version: number; enabled: boolean; states: Record<string, string>; revision: number }[];
  actions: Record<string, unknown>[];
  diagnostics: Record<string, unknown>[];
}
