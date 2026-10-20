import { LogOut } from 'lucide-react';
import { t, useLocale } from '../i18n';
import { currentHostedSession, signOutHosted } from './hostedSession';
import './hosted.css';

export function AccountButton() {
  useLocale();
  const session = currentHostedSession();
  if (!session?.authenticated) return null;
  const name = session.user?.display_name || session.user?.username;
  return <span className="hosted-account">
    <span title={name}>{name}</span>
    <button type="button" className="secondary-button" aria-label={t('Sign out')} title={`${name} · ${t('Sign out')}`} onClick={() => { void signOutHosted(); }}><LogOut size={14} /><span className="hosted-signout-label">{t('Sign out')}</span></button>
  </span>;
}
