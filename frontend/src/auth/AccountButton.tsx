import { useEffect, useState } from 'react';
import { LogOut, UserCog, Users } from 'lucide-react';
import { t, useLocale } from '../i18n';
import { currentHostedSession, signOutHosted, subscribeHostedConnection } from './hostedSession';
import { AccountSettingsModal, AdminUsersModal } from './AccountDialogs';
import './hosted.css';

export function AccountButton() {
  const locale = useLocale(s => s.locale);
  const zh = locale === 'zh-CN';
  const [, setTick] = useState(0);
  const [dialog, setDialog] = useState<null | 'account' | 'admin'>(null);

  useEffect(() => subscribeHostedConnection(() => setTick(v => v + 1)), []);

  const session = currentHostedSession();
  if (!session?.authenticated) return null;
  const name = session.user?.display_name || session.user?.username || '';
  const isAdmin = session.user?.role === 'admin';

  return (
    <>
      <span className="hosted-account">
        <button
          type="button"
          className="secondary-button hosted-account-trigger"
          aria-label={zh ? '账号设置' : 'Account settings'}
          title={`${name} · ${zh ? '账号设置' : 'Account settings'}`}
          onClick={() => setDialog('account')}
        >
          <UserCog size={14} />
          <span className="hosted-action-label">{name}</span>
        </button>
        {isAdmin && (
          <button
            type="button"
            className="secondary-button"
            aria-label={zh ? '用户管理' : 'User management'}
            title={zh ? '用户管理' : 'User management'}
            onClick={() => setDialog('admin')}
          >
            <Users size={14} />
            <span className="hosted-action-label">{zh ? '用户管理' : 'Users'}</span>
          </button>
        )}
        <button
          type="button"
          className="secondary-button"
          aria-label={t('Sign out')}
          title={`${name} · ${t('Sign out')}`}
          onClick={() => { void signOutHosted(); }}
        >
          <LogOut size={14} />
          <span className="hosted-signout-label">{t('Sign out')}</span>
        </button>
      </span>
      {dialog === 'account' && <AccountSettingsModal onClose={() => setDialog(null)} />}
      {dialog === 'admin' && isAdmin && <AdminUsersModal onClose={() => setDialog(null)} />}
    </>
  );
}
