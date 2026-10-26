import { ExternalLink } from 'lucide-react';
import { t } from '../i18n';
import './packPublishing.css';

export const MARKETPLACE_PUBLISH_URL = 'https://open-agent-world.space/marketplace/publish/new';
// backend/packs/manifest.py PACK_ID; escape hyphens for HTML pattern's v flag.
export const PACK_ID_PATTERN = String.raw`[a-z][a-z0-9\-]*(\.[a-z][a-z0-9\-]*)+`;

export function PackIdGuidance() {
  return <p>{t('For Marketplace, use a namespace you are authorized to publish under, for example your-namespace.my-pack. Check your namespace on the Marketplace author website. Author names do not grant permission. Keep local.* IDs for local or offline exports.')}</p>;
}

export function exportedPackFile(blob: Blob, manifest: { id: string; version: string }): File {
  // The existing export endpoints validate the archive, including checksums.
  if (!(blob instanceof Blob) || !blob.size || blob.type.split(';')[0] !== 'application/vnd.oaw.pack') {
    throw new Error(t('No valid Pack was exported. Check the Pack and export it again.'));
  }
  return new File([blob], `${manifest.id}-${manifest.version}.oawpack`, { type: blob.type });
}

export function MarketplacePublish({ file, packId, busy = false }: { file?: File; packId: string; busy?: boolean }) {
  const ready = !!file && !busy && !packId.startsWith('local.');
  return <div className="pack-publishing">
    {ready ? <a className="secondary-button" href={MARKETPLACE_PUBLISH_URL} target="_blank" rel="noopener noreferrer">
      <ExternalLink size={14} />{t('Publish on Marketplace')}</a>
      : <button className="secondary-button" type="button" disabled><ExternalLink size={14} />{t('Publish on Marketplace')}</button>}
    <p>{t('Check and export the Pack first. On the website, sign in and select the saved .oawpack file yourself. Opening the page does not upload the file. Submission enters review; it does not immediately become public.')}</p>
    {packId.startsWith('local.') && <p>{t('To submit this Pack, replace its local.* ID with an ID under your authorized Marketplace namespace, then check and export again.')}</p>}
    {ready && <>
      <p>{t('Select this exported file on the website:')} <strong>{file.name}</strong></p>
      <p>{t('If the page does not open, copy this address into your browser:')} <code>{MARKETPLACE_PUBLISH_URL}</code></p>
    </>}
  </div>;
}
