import { t, useLocale } from '../i18n';

/** Part of the card boundary, not a mode switch. The canvas owns its gesture. */
export function CardMoveGrip() {
  useLocale();
  return <span className="touch-card-grip" title={t('Hold to move card')} aria-hidden="true" />;
}
