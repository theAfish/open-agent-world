// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { useState } from 'react';
import { t, useLocale } from './index';
import messages from './messages.json';
import { STEPS, CHAPTERS } from '../onboarding/steps';

afterEach(() => { cleanup(); useLocale.getState().setLocale('en'); vi.restoreAllMocks(); });

it('switches live copy and accessibility labels without discarding an input draft', () => {
  useLocale.getState().setLocale('en');
  function Draft() {
    useLocale();
    const [draft, setDraft] = useState('');
    return <><label>{t('Deck name')}<input aria-label={t('Deck name')} value={draft} onChange={event => setDraft(event.target.value)} /></label><button>{t('Save')}</button></>;
  }
  render(<Draft />);
  const input = screen.getByRole('textbox');
  fireEvent.change(input, { target: { value: 'My 用户卡组' } });
  act(() => useLocale.getState().setLocale('zh-CN'));
  expect(screen.getByRole('textbox', { name: '卡组名称' })).toBe(input);
  expect((input as HTMLInputElement).value).toBe('My 用户卡组');
  expect(screen.getByRole('button', { name: '保存' })).toBeTruthy();
  expect(localStorage.getItem('oaw.locale')).toBe('zh-CN');
  act(() => useLocale.getState().setLocale('en'));
  expect(screen.getByRole('button', { name: 'Save' })).toBeTruthy();
});

it('keeps interpolation values literal and tolerates unavailable storage', () => {
  vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('disabled'); });
  useLocale.getState().setLocale('zh-CN');
  expect(t('Place {v0}', { v0: '<script>$&{v0}</script>' })).toBe('放置 <script>$&{v0}</script>');
  expect(t('custom.plugin.untranslated')).toBe('custom.plugin.untranslated');
  useLocale.getState().setLocale('en');
  expect(t('地图册')).toBe('Atlas');
});

it('covers every tutorial message and keeps placeholders in both locales', () => {
  const dictionary: Record<string, string[]> = messages;
  const tutorialCopy = [...CHAPTERS, ...STEPS.flatMap(step => [step.dialogue, step.hint, step.button, step.optional].filter((value): value is string => !!value))];
  for (const key of tutorialCopy) expect(dictionary[key], key).toHaveLength(2);
  for (const [key, pair] of Object.entries(dictionary)) {
    expect(pair.every(text => !!text.trim()), key).toBe(true);
    const tokens = (value: string) => [...value.matchAll(/\{(\w+)\}/g)].map(match => match[1]).sort();
    expect(tokens(pair[1]), key).toEqual(tokens(pair[0]));
  }
});
