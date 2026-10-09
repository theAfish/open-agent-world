import { useEffect, useState } from 'react';
import { Download, File, RefreshCw } from 'lucide-react';
import { Modal } from '../components/Modal';
import { apiErrorMessage } from '../api/client';
import { t } from '../i18n';

export interface TextPreview { state: string; text?: string; truncated?: boolean }
export function FilePreview({ name, imageUrl, downloadUrl, loadText, onClose }: {
  name: string; imageUrl?: string; downloadUrl: string;
  loadText: (signal: AbortSignal) => Promise<TextPreview>; onClose: () => void;
}) {
  const [value, setValue] = useState<TextPreview>(), [error, setError] = useState(''), [retry, setRetry] = useState(0);
  useEffect(() => {
    if (imageUrl) return;
    const controller = new AbortController();
    setValue(undefined); setError('');
    void loadText(controller.signal).then(result => { if (!controller.signal.aborted) setValue(result); })
      .catch(reason => { if (!controller.signal.aborted) setError(apiErrorMessage(reason)); });
    return () => controller.abort();
  }, [imageUrl, loadText, retry]);
  return <Modal title={t('Preview {v0}', { v0: name })} onClose={onClose} className="file-preview-modal" actions={
    <a className="secondary-button" href={downloadUrl} download={name}><Download size={14} />{t('Download')}</a>
  }>
    {imageUrl ? <img src={imageUrl} alt={name} onError={() => setError(t('Preview unavailable'))} />
      : value?.state === 'text' ? <pre>{value.text}</pre>
      : error ? null : value ? <p><File size={18} /> {t('Preview unavailable')}</p> : <p role="status">{t('Loading…')}</p>}
    {value?.truncated && <p role="status">{t('Preview truncated')}</p>}
    {error && <div role="alert"><p className="ui-error">{error}</p>{!imageUrl && <button className="secondary-button" onClick={() => setRetry(value => value + 1)}><RefreshCw size={14} />{t('Retry')}</button>}</div>}
  </Modal>;
}
