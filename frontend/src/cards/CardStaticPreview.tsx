import { Image as ImageIcon, MessageSquare } from 'lucide-react';
import { t } from '../i18n';
import { surfaceDraftKey, useNodeSurfaceStore } from '../state/nodeSurfaces';
import { useWorldStore } from '../state/worldStore';
import type { NodeTypeCatalogItem, WorldCard } from '../types/world';
import { staticWorkspaceImage } from './staticWorkspaceImage';

const bounded = (value: unknown, fallback = '', limit = 360) => typeof value === 'string' && value.trim()
  ? value.slice(0, limit) : fallback;

/** Read existing model data only. No editor, plugin slot, runtime subscription,
 * document parsing, file request, screenshot capture or fabricated content. */
export function CardStaticPreview({ card, definition, workspace, width, height }: {
  card: WorldCard; definition?: NodeTypeCatalogItem; workspace: boolean; width?: number; height?: number;
}) {
  if (workspace && card.type === 'sandbox') return <SandboxStaticPreview card={card} width={width} height={height} />;
  const agent = card.type === 'agent' || definition?.traits.includes('core.agent');
  const text = bounded(card.type === 'text' ? card.config.preview ?? card.config.content
    : agent ? card.config.system_instruction : card.config.summary ?? card.config.description,
  t(card.type === 'text' ? 'Empty managed text resource.' : agent ? 'Ready for a scoped instruction.'
    : card.type === 'conversation' ? 'A shared field for durable conversations.' : definition?.description ?? 'Plugin-defined world object.'));
  const filename = bounded(card.config.filename, card.name);
  const image = card.type === 'image';
  const content = <>
    {image ? <div className="card-static-image">
      {typeof card.config.preview_url === 'string' ? <img src={card.config.preview_url} alt="" draggable={false} decoding="async" loading="lazy" /> : <span data-material-layer="top-print"><ImageIcon size={40} /></span>}
      <span data-material-layer="top-print">{filename}</span>
    </div> : <p data-material-layer="top-print" className={card.type === 'text' ? 'card-static-document' : ''}>{text}</p>}
    <div className="card-static-metadata" data-material-layer="top-print"><span>{agent ? bounded(card.config.model, t('Default')) : card.type === 'text' ? filename : t(definition?.label ?? card.type)}</span>
      <span>{card.type === 'text' ? `r${Number(card.config.revision ?? 0)}` : t(card.status)}</span></div>
  </>;
  if (!workspace) return <div className="card-static-preview"><div className="node-preview-body">{content}</div></div>;
  return <div className={`card-static-workspace ${card.type === 'conversation' ? 'is-conversation' : ''}`}>
    <div className="card-static-toolbar"><span>{t(agent ? 'Activity' : 'Workspace')}</span><span>{t(card.status)}</span></div>
    <div className="card-static-workspace-body">
      <aside><MessageSquare size={16} /><strong>{t(agent ? 'Conversation history' : 'Workspace')}</strong><span>{card.name}</span></aside>
      <main>{content}{card.type === 'conversation' && <div className="card-static-composer">{t('Message')}</div>}</main>
    </div>
  </div>;
}

function SandboxStaticPreview({ card, width, height }: { card: WorldCard; width?: number; height?: number }) {
  const theme = useWorldStore(s => s.theme);
  const drafts = useNodeSurfaceStore.getState().drafts;
  const info = useWorldStore.getState().sandboxInfo[card.id];
  const scope = [card.config.runtime, card.config.workspace_path, card.config.workspace_access, false];
  let selection: { root?: string; label?: string; path?: string } | undefined;
  try { selection = JSON.parse(drafts[surfaceDraftKey(card.id, 'sandbox-selection', ...scope)] ?? 'null'); } catch { /* Old drafts can be absent or invalid. */ }
  let snapshot: { root?: string; path?: string; text?: string } | undefined;
  try { snapshot = JSON.parse(drafts[surfaceDraftKey(card.id, 'sandbox-static-preview', ...scope)] ?? 'null'); } catch { /* Older sessions have no projection. */ }
  const fileText = selection && snapshot?.root === selection.root && snapshot?.path === selection.path ? bounded(snapshot?.text, '', 1600) : '';
  const command = bounded(drafts[`sandbox:${card.id}`] ?? card.config.active_command, '', 180);
  const output = Array.isArray(card.config.output) ? card.config.output.slice(-8).map(line => String(line).slice(-160)).join('\n').slice(-640) : '';
  const runtime = bounded(info?.runtime_id ?? card.config.runtime, 'auto');
  const path = bounded(info?.workspace_path ?? card.config.workspace_path, t('Workspace'));
  const settings = drafts[`sandbox-tab:${card.id}`] === 'settings';
  const sidebar = Math.min(42, Math.max(18, Number(drafts[`sandbox-sidebar:${card.id}`] ?? 224) / 1020 * 100));
  const terminal = Math.min(65, Math.max(28, Number(drafts[`sandbox-terminal:${card.id}`] ?? 42)));
  const src = staticWorkspaceImage({ runtime, path, filename: bounded(selection?.label), fileText, output, command,
    status: t(card.status), settings, sidebar, terminal, readOnly: card.config.workspace_access === 'read_only', network: !!card.config.network_enabled,
    labels: { workspace: t('Workspace'), settings: t('Settings'), files: t('Files'), preview: t('Preview'), terminal: t('Terminal'), history: t('History'),
      empty: t('Select a file to preview'), output: t('No output yet.'), runtime: t('Runtime'), access: t('Workspace access'), network: t('Network'),
      enabled: t('Enabled'), disabled: t('Disabled'), readOnly: t('Read only'), readWrite: t('Read & write') },
  }, theme, width, height ? height - 40 : undefined);
  return <img className="card-static-workspace-image" data-static-preview="sandbox" src={src} draggable={false}
    alt={`${t(settings ? 'Settings' : 'Workspace')} · ${runtime} · ${path}`} />;
}
