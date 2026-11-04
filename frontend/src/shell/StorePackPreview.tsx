import { t } from '../i18n';
import type { ReactNode } from 'react';
import type { StorePreview } from '../types/packs';
import { CatalogIcon } from '../components/CatalogIcon';
import { PackSurface } from './PackSurface';
import { PACK_DESIGNS, packPackaging } from './packDesign';

const MAX_IMAGE_BYTES = 256 * 1024;
function previewImage(value?: string | null) {
  if (typeof value !== 'string' || value.length > 4 * Math.ceil(MAX_IMAGE_BYTES / 3) + 64) return undefined;
  const data = /^data:image\/(?:png|jpeg|webp|gif|svg\+xml);base64,([A-Za-z0-9+/]+={0,2})$/.exec(value)?.[1];
  if (!data || data.length % 4) return undefined;
  const bytes = data.length / 4 * 3 - (data.endsWith('==') ? 2 : data.endsWith('=') ? 1 : 0);
  return bytes > 0 && bytes <= MAX_IMAGE_BYTES ? value : undefined;
}

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
  const icon = typeof pack.preview?.icon === 'string' && /^[a-z][a-z0-9-]{0,63}$/.test(pack.preview.icon) ? pack.preview.icon : undefined;
  const iconUrl = previewImage(pack.preview?.icon_url);
  const artwork = previewImage(pack.preview?.artwork_url);
  const cards = icon || iconUrl ? [{ id: `${pack.id}.preview`, label: pack.name,
    icon: <CatalogIcon definition={{ icon: icon ?? 'package', icon_url: iconUrl }} size={38} />, iconUrl,
  }] : [];
  return <PackSurface definition={{ id: pack.id, plugin_id: pack.id, name: pack.name,
    description: pack.summary, cards: [], packaging, artwork_url: artwork,
    accent_color: typeof color === 'string' && /^#[0-9a-f]{6}$/i.test(color) ? color : PACK_DESIGNS[packaging].color,
  }} edition={pack.latest_version ? `v${pack.latest_version}` : t('No version available')}
    cards={cards} count={typeof count === 'number' && Number.isSafeInteger(count) && count >= 0 && count <= 100 ? count : null}
    version={pack.latest_version ?? undefined} notice={pack.restart_required ? { kind: 'restart', label: 'Restart required' }
      : pack.update_available ? { kind: 'update', label: 'Update available' } : pack.installed_version ? { kind: 'info', label: 'Installed' } : undefined}
    opened={false} label={label ?? t('View {v0} details', { v0: pack.name })}
    onClick={onClick} caption={caption} />;
}
