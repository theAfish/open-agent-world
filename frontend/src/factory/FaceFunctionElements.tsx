import type { FaceDesign, FaceElement, InputField, Scalar, SurfaceDesign } from './types';
import type { NodeSurfaceLevel } from '../types/world';
import { FACE_MODES, MODE_LABELS } from './faceDesign';
import { buttonAction, buttonLabel } from './faceButtons';

const SAMPLE_FIELDS: InputField[] = [
  { key: 'a', label: '输入字段 A', type: 'text', default: '', required: false },
  { key: 'b', label: '输入字段 B', type: 'text', default: '', required: false },
];

export function FaceFieldInput({ field, value, onChange }: {
  field: InputField; value: Scalar; onChange?: (value: Scalar) => void;
}) {
  return <label className={field.type === 'boolean' ? 'factory-check' : ''}>
    <span>{field.label}{field.required && ' *'}</span>
    {field.type === 'boolean'
      ? <input type="checkbox" checked={Boolean(value)} readOnly={!onChange} tabIndex={onChange ? undefined : -1}
        onChange={onChange ? event => onChange(event.target.checked) : undefined} />
      : <input type={field.type === 'number' ? 'number' : 'text'} step="any" maxLength={10000} required={field.required}
        value={String(value)} readOnly={!onChange} tabIndex={onChange ? undefined : -1}
        onChange={onChange ? event => onChange(field.type === 'number' && event.target.value !== '' ? Number(event.target.value) : event.target.value) : undefined} />}
  </label>;
}

/** Designer proofs and live cards share the exact functional print and controls. */
export function FaceFunctionElement({ element, face, surface, fields = SAMPLE_FIELDS, values = {}, onChange, onAction, level, busy = false, disabled = false, result, thumbnail = false }: {
  element: FaceElement; face: FaceDesign; surface: SurfaceDesign; fields?: InputField[];
  values?: Record<string, Scalar>; onChange?: (key: string, value: Scalar) => void;
  busy?: boolean; disabled?: boolean; result?: Scalar; thumbnail?: boolean;
  level?: NodeSurfaceLevel; onAction?: (element: FaceElement, target?: NodeSurfaceLevel) => void;
}) {
  if (element.kind === 'fields') return <div className="factory-fields factory-face-fields nodrag nopan nowheel" data-layout={surface.field_layout}>
    {fields.map(field => thumbnail
      ? <span key={field.key} className={`factory-face-field-placeholder ${field.type === 'boolean' ? 'factory-check' : ''}`}>
        <span>{field.label}{field.required && ' *'}</span>
        <span className="factory-face-input-placeholder" data-type={field.type}>{field.type === 'boolean' ? (values[field.key] ?? field.default) ? '✓' : '' : String(values[field.key] ?? field.default)}</span>
      </span>
      : <FaceFieldInput key={field.key} field={field} value={values[field.key] ?? field.default}
        onChange={onChange ? value => onChange(field.key, value) : undefined} />)}
  </div>;
  const action = buttonAction(element), label = buttonLabel(element, face);
  if (element.kind === 'button') {
    const className = 'factory-face-button nodrag nopan';
    const style = { background: element.button?.background, borderRadius: element.button?.radius };
    if (thumbnail) return <span className={className} style={style}>{label}</span>;
    if (action === 'surface') return <select className={className} style={style} aria-label={label} value={level ?? face.studio?.initial ?? 'preview'}
      disabled={disabled} tabIndex={onAction ? undefined : -1} onChange={event => onAction?.(element, event.target.value as NodeSurfaceLevel)}>
      {(face.studio?.enabled ?? FACE_MODES).map(mode => <option key={mode} value={mode}>{MODE_LABELS[mode]}</option>)}
    </select>;
    return <button type={action === 'run' && onChange ? 'submit' : 'button'} className={className} style={style}
      disabled={disabled || (action === 'run' && busy) || action === 'custom'} tabIndex={onAction ? undefined : -1}
      title={action === 'custom' ? '自定义逻辑挂载尚未启用' : undefined}
      onClick={event => { event.stopPropagation(); if (action !== 'run') onAction?.(element); }}>
      {action === 'run' && busy ? '运行中…' : label}</button>;
  }
  if (thumbnail && element.kind === 'action') return <span className="factory-run factory-face-action" style={{ background: face.color }}>{face.button_label}</span>;
  if (thumbnail && element.kind === 'result') return <span className="factory-result factory-face-result">{result === undefined ? '等待运行' : String(result)}</span>;
  if (element.kind === 'action') return <button type={onChange ? 'submit' : 'button'} className="factory-run factory-face-action nodrag nopan"
    disabled={busy || disabled} tabIndex={onChange ? undefined : -1} style={{ background: face.color }}>{busy ? '运行中…' : face.button_label}</button>;
  if (element.kind === 'result') return <output className="factory-result factory-face-result nodrag nopan nowheel" aria-live={onChange ? 'polite' : undefined}>
    {result === undefined ? '等待运行' : String(result)}</output>;
  return null;
}
