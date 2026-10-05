import { useEffect, useState } from 'react';
import { CardFace, CardStock } from '../components/CardFace';
import { CatalogIcon } from '../components/CatalogIcon';
import { apiErrorMessage, worldApi } from '../api/client';
import type { PluginViewProps } from '../plugins/sdk';
import type { FaceDesign, FunctionDesign, InputField, PrintedDesign, Scalar } from './types';
import { collapsedSurface, useNodeSurfaceStore } from '../state/nodeSurfaces';
import { useWorldStore } from '../state/worldStore';
import type { NodeSurfaceLevel, WorldCard } from '../types/world';
import { FaceArtwork } from './FaceArtwork';
import { FaceFunctionElement } from './FaceFunctionElements';
import { buttonAction } from './faceButtons';
import './factory.css';

export function FacePreview({ face }: { face: FaceDesign }) {
  if (face.studio) {
    const surface = face.studio.modes[face.studio.initial]!;
    const scale = Math.min(240 / surface.width, 260 / surface.height);
    return <div className="factory-face-preview"><div style={{ width: surface.width * scale, height: surface.height * scale }}>
      <div style={{ width: surface.width, height: surface.height, transform: `scale(${scale})`, transformOrigin: 'top left' }}><FaceArtwork face={face} surface={surface} sample /></div>
    </div></div>;
  }
  return <div className="factory-face-preview"><CardStock finish={face.finish} size="standard">
    <CardFace label={face.title} description={face.description} variant={face.variant} tone={face.tone}
      icon={<CatalogIcon definition={{ icon: face.icon }} size={42} />} />
  </CardStock></div>;
}

export function FieldInput({ field, value, onChange }: { field: InputField; value: Scalar; onChange: (value: Scalar) => void }) {
  return <label className={field.type === 'boolean' ? 'factory-check' : ''}>{field.label}{field.required && ' *'}
    {field.type === 'boolean' ? <input type="checkbox" checked={Boolean(value)} onChange={event => onChange(event.target.checked)} />
      : <input type={field.type === 'number' ? 'number' : 'text'} step="any" maxLength={10000} required={field.required}
        value={String(value)} onChange={event => onChange(field.type === 'number' && event.target.value !== '' ? Number(event.target.value) : event.target.value)} />}
  </label>;
}

export function FunctionForm({ design, face, run }: {
  design: FunctionDesign; face?: FaceDesign; run: (values: Record<string, Scalar>) => Promise<Scalar>;
}) {
  const [values, setValues] = useState<Record<string, Scalar>>({});
  const [result, setResult] = useState<Scalar>();
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  useEffect(() => { setValues({}); setResult(undefined); setError(''); }, [design]);
  return <form className="factory-function" data-layout={face?.layout ?? 'stack'} onSubmit={event => {
    event.preventDefault(); setBusy(true); setError(''); setResult(undefined);
    void run(values).then(setResult).catch(cause => setError(apiErrorMessage(cause))).finally(() => setBusy(false));
  }}>
    {face?.help_text && <p>{face.help_text}</p>}
    <div className="factory-fields">{design.fields.map(field => <FieldInput key={field.key} field={field}
      value={values[field.key] ?? field.default} onChange={value => setValues(current => ({ ...current, [field.key]: value }))} />)}</div>
    <button disabled={busy} type="submit">{busy ? '运行中…' : face?.button_label ?? '试运行'}</button>
    {error && <p role="alert" className="factory-error">{error}</p>}
    {result !== undefined && <output className="factory-result">{String(result)}</output>}
  </form>;
}

export function PrintedCardView({ card, level }: PluginViewProps) {
  const design = card.config as unknown as PrintedDesign;
  if (design.face.studio) return <DesignedCard card={card} level={level} />;
  if (level === 'preview') return <div className="factory-preview nodrag nowheel"><strong>{design.face.title}</strong><small>{design.face.description}</small>
    <button type="button" onClick={() => useNodeSurfaceStore.getState().openWorkspace(card.id)}>{design.face.button_label}</button></div>;
  return <section className="factory nodrag nowheel" data-testid="printed-card" data-level={level}>
    <FacePreview face={design.face} />
    <FunctionForm design={design.function} face={design.face}
      run={async values => (await worldApi.factory<{ result: Scalar }>(card.id, 'run', { values })).result} />
  </section>;
}

/** The same artwork used by the designer, with live fields/actions in its authored regions. */
export function DesignedCard({ card, level, staticView = false }: { card: WorldCard; level: NodeSurfaceLevel; staticView?: boolean }) {
  const design = card.config as unknown as PrintedDesign, studio = design.face.studio!;
  const surface = studio.modes[level] ?? studio.modes[studio.initial]!;
  const [values, setValues] = useState<Record<string, Scalar>>({}), [result, setResult] = useState<Scalar>();
  const [error, setError] = useState(''), [busy, setBusy] = useState(false);
  useEffect(() => { setValues({}); setResult(undefined); setError(''); }, [design]);
  const base = useNodeSurfaceStore(state => state.baseLevels[card.id]);
  const collapsed = collapsedSurface({ states: studio.enabled, initial: studio.initial, open: studio.open }, level, base);
  return <div className="factory-designed-card" data-testid="printed-card" data-workspace-node-id={level === 'workspace' ? card.id : undefined}>
    <form className="factory-designed-form" onSubmit={event => {
      event.preventDefault(); if (busy || staticView) return; setBusy(true); setError('');
      void worldApi.factory<{ result: Scalar }>(card.id, 'run', { values }).then(response => setResult(response.result))
        .catch(cause => setError(apiErrorMessage(cause))).finally(() => setBusy(false));
    }}>
      <FaceArtwork face={design.face} surface={surface} functionDesign={design.function} render={element =>
        ['fields', 'action', 'button', 'result'].includes(element.kind)
          ? <FaceFunctionElement element={element} face={design.face} surface={surface} fields={design.function.fields}
            values={values} onChange={(key, value) => setValues(current => ({ ...current, [key]: value }))}
            busy={busy} level={level} disabled={staticView || (buttonAction(element) === 'collapse' && collapsed === level)
              || (buttonAction(element) === 'open' && studio.open === level)} result={result}
            onAction={(button, target) => {
              if (staticView) return;
              const action = buttonAction(button), store = useNodeSurfaceStore.getState();
              if (action === 'open') store.selectSurface(card.id, studio.open);
              if (action === 'collapse') store.selectSurface(card.id, collapsed);
              if (action === 'surface' && target && studio.enabled.includes(target)) store.selectSurface(card.id, target);
              if (action === 'delete') void useWorldStore.getState().deleteCard(card.id).catch(cause => setError(apiErrorMessage(cause)));
            }} />
          : undefined} />
    </form>
    {error && <p className="factory-runtime-error nodrag" role="alert">{error}</p>}
    {result !== undefined && surface.elements.some(element => buttonAction(element) === 'run') && !surface.elements.some(element => element.kind === 'result')
      && <output className="factory-runtime-error factory-runtime-result nodrag">{String(result)}</output>}
  </div>;
}
