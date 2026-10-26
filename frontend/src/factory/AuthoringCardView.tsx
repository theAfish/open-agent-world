import { useEffect, useMemo, useRef, useState } from 'react';
import { apiErrorMessage } from '../api/client';
import type { PluginViewProps } from '../plugins/sdk';
import { useNodeSurfaceStore, collapsedSurface } from '../state/nodeSurfaces';
import { useWorldStore } from '../state/worldStore';
import type { NodeSurfaceLevel } from '../types/world';
import { FaceArtwork } from './FaceArtwork';
import { FaceFunctionElement } from './FaceFunctionElements';
import { faceStudio } from './faceDesign';
import { buttonAction, buttonLabel } from './faceButtons';
import type { CardInterface } from './projectTypes';
import type { FaceElement, Scalar } from './types';

/** Shared artwork and bindings for installed cards and the bounded project preview. */
export function AuthoringPreview({ design, level, status = 'idle', nodeId, initialValues, run, onNavigate, onDelete }: {
  design: CardInterface; level: NodeSurfaceLevel; status?: string; nodeId?: string;
  initialValues?: Record<string, Scalar>;
  run?: (action: string, values: Record<string, Scalar>) => Promise<unknown>;
  onNavigate?: (action: 'open' | 'collapse' | 'surface', target?: NodeSurfaceLevel) => void;
  onDelete?: () => Promise<void>;
}) {
  const studio = faceStudio(design.face), surface = studio.modes[level] ?? studio.modes[studio.initial]!;
  const [values, setValues] = useState<Record<string, Scalar>>({}), [result, setResult] = useState<unknown>();
  const [busy, setBusy] = useState(false), [error, setError] = useState('');
  const active = useRef(false);
  useEffect(() => { setValues(initialValues ?? {}); setResult(undefined); setError(''); }, [design, initialValues]);
  const invoke = async (action: string) => {
    if (!run || active.current) return;
    active.current = true; setBusy(true); setError('');
    try { setResult(await run(action, Object.fromEntries(design.function.fields.map(field => [field.key, values[field.key] ?? field.default])))); }
    catch (cause) { setError(apiErrorMessage(cause)); }
    finally { active.current = false; setBusy(false); }
  };
  const renderedResult = result === undefined ? undefined : typeof result === 'object' ? JSON.stringify(result, null, 2) : String(result);
  const customAction = (element: FaceElement) => design.function.button_bindings?.find(binding => binding.mode === level && binding.element_id === element.id)?.logic_id;
  return <div className="factory-designed-card authoring-card" data-testid="authoring-card" data-workspace-node-id={level === 'workspace' ? nodeId : undefined} style={{ width: surface.width, height: surface.height }}>
    <form className="factory-designed-form" onSubmit={event => { event.preventDefault(); void invoke(design.action); }}>
      <FaceArtwork face={design.face} surface={surface} level={level} functionDesign={design.function} render={element => {
        if (element.kind === 'status') return <span role="status">{busy ? 'Running…' : error ? 'Failed' : result !== undefined ? 'Complete' : status}</span>;
        if (buttonAction(element) === 'custom') {
          const action = customAction(element);
          return <button type="button" className="factory-face-button nodrag nopan" disabled={!run || !action || busy}
            title={action ? `Action: ${action}` : 'Bind this button to an action in Function mode.'}
            style={{ background: element.button?.background, borderRadius: element.button?.radius }}
            onClick={() => { if (action) void invoke(action); }}>{buttonLabel(element, design.face)}</button>;
        }
        if (!['fields', 'action', 'button', 'result'].includes(element.kind)) return undefined;
        return <FaceFunctionElement element={element} face={design.face} surface={surface} fields={design.function.fields}
          values={values} onChange={(key, value) => setValues(current => ({ ...current, [key]: value }))}
          busy={busy} level={level} disabled={buttonAction(element) === 'run' ? !run : buttonAction(element) === 'delete' ? !onDelete : !onNavigate}
          result={renderedResult} onAction={(button, target) => {
            const action = buttonAction(button);
            if (action === 'open' || action === 'collapse' || action === 'surface') onNavigate?.(action, target);
            if (action === 'delete') void onDelete?.().catch(cause => setError(apiErrorMessage(cause)));
          }} />;
      }} />
    </form>
    {error && <p className="factory-runtime-error" role="alert">{error}</p>}
    {result !== undefined && !surface.elements.some(element => element.kind === 'result') && <output className="factory-runtime-error factory-runtime-result">{renderedResult}</output>}
  </div>;
}

export function AuthoringCardView({ card, definition, level, host }: PluginViewProps) {
  const design = definition.authoring!;
  const studio = faceStudio(design.face);
  const base = useNodeSurfaceStore(state => state.baseLevels[card.id]);
  const initialValues = useMemo(() => Object.fromEntries(design.function.fields.flatMap(field => {
    const value = card.config[field.key];
    return typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean' ? [[field.key, value]] : [];
  })), [design, card.config]);
  return <AuthoringPreview design={design} level={level} status={card.status} nodeId={card.id} initialValues={initialValues}
    run={async (action, values) => {
      const response = await host.resourceAction(action, values);
      return design.result_key ? response[design.result_key] : response;
    }} onNavigate={(action, target) => {
      const next = action === 'open' ? studio.open : action === 'collapse'
        ? collapsedSurface({ states: studio.enabled, initial: studio.initial, open: studio.open }, level, base) : target;
      if (next && studio.enabled.includes(next)) useNodeSurfaceStore.getState().selectSurface(card.id, next);
    }} onDelete={() => useWorldStore.getState().deleteCard(card.id)} />;
}
