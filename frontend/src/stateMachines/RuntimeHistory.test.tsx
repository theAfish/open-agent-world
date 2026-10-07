// @vitest-environment jsdom
import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, expect, it } from 'vitest';
import { RuntimeHistory } from './RuntimeHistory';
import { useLocale } from '../i18n';

afterEach(cleanup);
it('collapses runtime details and omits unrelated event noise', () => {
  useLocale.setState({locale: 'en'});
  render(<RuntimeHistory entities={[]} actions={[]} diagnostics={[
    {reason: 'unrelated_event'},
    {reason: 'transition_committed', system_transitions: [{entity_id: 'status', from_state: 'Running', state_id: 'Idle'}]},
  ]} />);
  expect(screen.queryByText('unrelated_event')).toBeNull();
  expect(screen.getByText('Running → Idle')).toBeTruthy();
  expect(screen.getByText('Run history').closest('details')?.open).toBe(false);
  expect(screen.getByText('Technical details').closest('details')?.open).toBe(false);
});
