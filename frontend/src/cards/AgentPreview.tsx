import { Brain, Cpu, FolderOpen, MessageSquarePlus, MessagesSquare, ShieldCheck } from 'lucide-react';
import { t, useLocale } from '../i18n';
import { useWorldStore } from '../state/worldStore';
import type { WorldCard } from '../types/world';
import { modelLabel } from './modelLabel';
import './agentPreview.css';

/** A read-only summary shared by the active card and its lightweight projection. */
export function AgentPreview({ card }: { card: WorldCard }) {
  useLocale();
  const models = useWorldStore(state => state.modelCatalog);
  const model = card.config.model === 'default' ? t('Default') : modelLabel(models, card.config.model);
  const project = typeof card.config.workspace_path === 'string' ? card.config.workspace_path.trim() : '';
  const projectName = project.replace(/[\\/]+$/, '').split(/[\\/]/).at(-1) || project;
  const effort = typeof card.config.reasoning_effort === 'string' && card.config.reasoning_effort !== 'default'
    ? card.config.reasoning_effort.trim() : '';
  const purpose = typeof card.config.description === 'string' ? card.config.description.trim() : '';
  const session = card.config.session_mode;
  const readOnly = card.config.workspace_access === 'read_only' || card.config.codex_sandbox === 'read-only';
  return <div className="agent-card-preview">
    {project && <div className="agent-preview-project" data-material-layer="top-print" title={project}>
      <FolderOpen size={15} aria-hidden="true" /><span>{projectName}</span>
    </div>}
    {purpose && <p className="agent-preview-purpose" data-material-layer="top-print">{purpose}</p>}
    <div className="agent-preview-tags" data-material-layer="top-print">
      <span className="agent-preview-tag" title={`${t('Model')}: ${model}`}><Cpu size={12} aria-hidden="true" /><span>{model}</span></span>
      {effort && <span className="agent-preview-tag" title={t('Reasoning effort')}><Brain size={12} aria-hidden="true" /><span>{t(effort)}</span></span>}
      {(session === 'continue' || session === 'fresh') && <span className="agent-preview-tag" title={t('Session mode')}>
        {session === 'fresh' ? <MessageSquarePlus size={12} aria-hidden="true" /> : <MessagesSquare size={12} aria-hidden="true" />}
        <span>{t(session === 'fresh' ? 'New session' : 'Continue')}</span>
      </span>}
      {readOnly && <span className="agent-preview-tag" title={t('Read only')}><ShieldCheck size={12} aria-hidden="true" /><span>{t('Read only')}</span></span>}
    </div>
  </div>;
}
