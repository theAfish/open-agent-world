import { t } from '../i18n';
import type { ReactNode } from 'react';
import type { StorePreview } from '../types/packs';
import { PackSurface } from './PackSurface';
import { PACK_DESIGNS, packPackaging } from './packDesign';

export interface StorePreviewPack {
  id: string; name: string; summary: string; latest_version: string | null;
  preview?: StorePreview | null;
  installed_version?: string | null; update_available?: boolean; restart_required?: boolean;
}

/** Catalog previews use the same geometry, materials and lifecycle as owned Packs. */
export function StorePackPreview({ pack, onClick, label, caption }: {
  pack: StorePreviewPack; onClick: () => void; label?: string; caption?: ReactNode;
}) {
  const packaging = packPackaging(pack.preview?.packaging);
  const color = pack.preview?.accent_color;
  const count = pack.preview?.count;
  return <PackSurface definition={{ id: pack.id, plugin_id: pack.id, name: pack.name,
    description: pack.summary, cards: [], packaging,
    accent_color: typeof color === 'string' && /^#[0-9a-f]{6}$/i.test(color) ? color : PACK_DESIGNS[packaging].color,
  }} edition={pack.latest_version ? `v${pack.latest_version}` : t('No version available')}
    cards={[]} count={typeof count === 'number' && Number.isSafeInteger(count) && count >= 0 && count <= 100 ? count : null}
    version={pack.latest_version ?? undefined} notice={pack.restart_required ? { kind: 'restart', label: 'Restart required' }
      : pack.update_available ? { kind: 'update', label: 'Update available' } : pack.installed_version ? { kind: 'info', label: 'Installed' } : undefined}
    opened={false} label={label ?? t('View {v0} details', { v0: pack.name })}
    onClick={onClick} caption={caption} />;
}
