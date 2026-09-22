import { createContext, useContext } from 'react';

/** Presentation capabilities only; the server independently enforces every operation. */
export type PluginDeploymentAccess = {
  config_fields: string[]; document_fields: string[]; summary_fields: string[];
  document_actions: string[]; downloads: string[]; resource_actions: Record<string, string[]>; execution: boolean;
};
export const WorkspaceAccess = createContext<{ deployed: boolean; permissions: Record<string, string[]>; plugin_access?: Record<string, PluginDeploymentAccess> }>({
  deployed: false, permissions: {},
});
export const useWorkspaceAccess = () => useContext(WorkspaceAccess);
