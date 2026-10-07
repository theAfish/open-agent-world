import { t } from '../i18n';
import type { StateMachineEventDescriptor } from './apiTypes';

/** Names and supported phases are host catalog data, including plugin registrations. */
export const eventLabel = (key: string, events: StateMachineEventDescriptor[] = []): string =>
  t(events.find(event => event.key === key)?.label ?? (key === 'unconfigured' ? 'Choose a trigger' : key));
