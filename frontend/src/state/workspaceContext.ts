import { useCallback, useEffect, useState } from 'react';
import { apiErrorMessage, worldApi } from '../api/client';
import { useWorldStore } from './worldStore';
import { useViewingSession } from './cardState';

export interface WorkspaceContext {
  bound?: boolean;
  claimed?: boolean;
  scope_type: 'shared' | 'session' | 'group'; scope_id: string; key: string; directory: string; label: string;
  writable_directory?: string | null;
  session_id: string; group_id: string; conversation_id: string | null; sandbox_id: string | null;
}

/** Group identity is resolved by the host from a real session, never guessed by a plugin. */
export function useWorkspaceContext(cardId: string, enabled = true): { value?: WorkspaceContext; error?: string; reload: () => void } {
  const [attempt, setAttempt] = useState(0);
  const reload = useCallback(() => setAttempt(value => value + 1), []);
  const session = useViewingSession(cardId, enabled);
  const signature = useWorldStore(state => {
    if (!enabled) return '';
    const card = state.cards.find(item => item.id === cardId);
    const agents = new Set(state.edges.filter(edge => edge.target === cardId).map(edge => edge.source));
    if (card?.equipment?.owner_id) agents.add(card.equipment.owner_id);
    const links = state.edges.filter(edge => edge.source === cardId || edge.target === cardId || agents.has(edge.source));
    const targets = new Set([cardId, ...links.map(edge => edge.target)]);
    const sandboxTypes = new Set(state.catalog.node_types.filter(type => type.traits.includes('core.sandbox')).map(type => type.id));
    return JSON.stringify([
      state.cards.filter(item => item.id === cardId || sandboxTypes.has(item.type)
        && (targets.has(item.id) || !!item.equipment && agents.has(item.equipment.owner_id)))
        .map(item => [item.id, item.config.workspace_scope, item.config.workspace_access, item.parent_id, item.equipment, state.sandboxInfo[item.id]?.runtime_locked]),
      state.edges.map(edge => [edge.id, edge.source, edge.target, edge.relationship, edge.direction, edge.missing_plugin]),
    ]);
  });
  const identityKey = JSON.stringify([cardId, session, signature, enabled]);
  const [result, setResult] = useState<{ identityKey: string; value?: WorkspaceContext; error?: string }>();
  const refreshEvent = useWorldStore(state => !enabled ? undefined : state.events.find(event =>
    (event.type === 'conversation_session_updated' && (!session || event.session_id === session))
    || ((event.sandbox_id === (result?.value?.sandbox_id ?? cardId) || event.node_id === cardId)
      && ['sandbox_command_started', 'sandbox_command_finished'].includes(event.type)))?.id);
  const requestKey = JSON.stringify([identityKey, attempt, refreshEvent]);
  useEffect(() => {
    if (!enabled) return;
    let live = true;
    void worldApi.workspaceContext(cardId, session).then(value => {
      if (live) setResult({ identityKey, value });
    }).catch(error => { if (live) setResult({ identityKey, error: apiErrorMessage(error) }); });
    return () => { live = false; };
  }, [requestKey]);
  return enabled && result?.identityKey === identityKey ? { ...result, reload } : { reload };
}
