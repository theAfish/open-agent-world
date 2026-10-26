import { Plus, Trash2 } from 'lucide-react';
import { FieldInput } from './PrintedCardView';
import type { FunctionDesign, InputField } from './types';

/** The same bounded function editor is used by projects and legacy Factory cards. */
export function FunctionControls({ value, onChange, fieldsOnly = false }: {
  value: FunctionDesign; onChange: (patch: Partial<FunctionDesign>) => void; fieldsOnly?: boolean;
}) {
  const updateField = (index: number, patch: Partial<InputField>) => onChange({
    fields: value.fields.map((field, i) => i === index ? { ...field, ...patch } : field),
  });
  return <div className="factory-controls">
    {value.fields.map((field, index) => <div className="factory-field-editor" key={index}>
      <div className="factory-row"><label>Field key<input maxLength={40} value={field.key} onChange={event => updateField(index, { key: event.target.value })} /></label>
        <label>Label<input maxLength={80} value={field.label} onChange={event => updateField(index, { label: event.target.value })} /></label>
        <button type="button" aria-label={`Remove field ${index + 1}`} onClick={() => onChange({ fields: value.fields.filter((_, i) => i !== index) })}><Trash2 size={14} /></button></div>
      <div className="factory-row"><label>Field type<select value={field.type} onChange={event => updateField(index, {
        type: event.target.value as InputField['type'], default: event.target.value === 'number' ? 0 : event.target.value === 'boolean' ? false : '',
      })}><option value="text">Text</option><option value="number">Number</option><option value="boolean">Boolean</option></select></label>
        <FieldInput field={{ ...field, label: 'Default value', required: false }} value={field.default} onChange={next => updateField(index, { default: next })} /></div>
      <label className="factory-check"><input type="checkbox" checked={field.required} onChange={event => updateField(index, { required: event.target.checked })} />Required</label>
    </div>)}
    <button type="button" disabled={value.fields.length >= 20} onClick={() => {
      let index = value.fields.length + 1; while (value.fields.some(field => field.key === `field${index}`)) index++;
      onChange({ fields: [...value.fields, { key: `field${index}`, label: `Field ${index}`, type: 'text', default: '', required: false }] });
    }}><Plus size={14} />Add field</button>
    {!fieldsOnly && <><label>Operation<select value={value.operation} onChange={event => onChange({ operation: event.target.value as FunctionDesign['operation'] })}>
      <option value="template">Text template</option><option value="sum">Sum numbers</option><option value="multiply">Multiply numbers</option><option value="join">Join values</option></select></label>
      {value.operation === 'template' && <label>Output template<textarea rows={4} maxLength={10000} value={value.template} onChange={event => onChange({ template: event.target.value })} /><small>Use {'{{field_key}}'} to insert an input value.</small></label>}
      {value.operation === 'join' && <label>Separator<input maxLength={100} value={value.separator} onChange={event => onChange({ separator: event.target.value })} /></label>}</>}
  </div>;
}
