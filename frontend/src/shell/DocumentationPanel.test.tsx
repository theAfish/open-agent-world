// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { useLocale } from '../i18n';
import { StrictMode } from 'react';
import { DocumentationPanel } from './DocumentationPanel';

beforeEach(() => {
  useLocale.setState({ locale: 'en' });
  vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('offline')));
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

it('navigates topics and relative links with working history without fetching content', () => {
  render(<DocumentationPanel />);
  fireEvent.click(screen.getByRole('link', { name: 'Build your first team' }));
  expect(screen.getByRole('heading', { name: 'Your first team', level: 1 })).toBeTruthy();
  const image = screen.getByRole('img', { name: 'Connecting cards and choosing a relationship' });
  expect(image.getAttribute('src')).toContain('connect-cards.gif');
  expect(image.getAttribute('src')).not.toMatch(/^https?:/);
  fireEvent.click(screen.getByRole('button', { name: 'Previous topic' }));
  expect(screen.getByRole('heading', { name: 'Use Open Agent World', level: 1 })).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: 'Next topic' }));
  expect(screen.getByRole('heading', { name: 'Your first team', level: 1 })).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: 'Previous topic' }));
  fireEvent.click(screen.getByRole('button', { name: 'Packs and cards' }));
  expect(screen.getByRole('heading', { name: 'Install a local Pack' })).toBeTruthy();
  expect((screen.getByRole('button', { name: 'Next topic' }) as HTMLButtonElement).disabled).toBe(true);
  expect(fetch).not.toHaveBeenCalled();
});

it('searches content, handles no results and preserves the current article', () => {
  render(<DocumentationPanel />);
  const search = screen.getByRole('searchbox', { name: 'Search documentation' });
  const nav = within(screen.getByRole('navigation', { name: 'Documentation topics' }));
  fireEvent.change(search, { target: { value: 'Install Pack from File' } });
  expect(nav.getByRole('button', { name: 'Packs and cards' })).toBeTruthy();
  expect(nav.queryByRole('button', { name: 'Models and settings' })).toBeNull();
  fireEvent.change(search, { target: { value: 'no-match-817234' } });
  expect(screen.getByRole('status').textContent).toBe('No documentation matches your search.');
  expect(screen.getByRole('heading', { name: 'Use Open Agent World', level: 1 })).toBeTruthy();
  fireEvent.change(search, { target: { value: '' } });
  expect(nav.getByRole('button', { name: 'Models and settings' })).toBeTruthy();
});

it('switches an open topic to Chinese and labels English-only topics', () => {
  render(<DocumentationPanel />);
  act(() => useLocale.setState({ locale: 'zh-CN' }));
  expect(screen.getByRole('heading', { name: '使用入门', level: 1 })).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: 'Models and settings' }));
  expect(screen.getByText('此主题目前提供英文文档。')).toBeTruthy();
  expect(screen.getByRole('button', { name: 'Models and settings' }).getAttribute('aria-current')).toBe('page');
});

it('follows section anchors and marks external destinations as online', () => {
  render(<StrictMode><DocumentationPanel initialPage="configuration.md" /></StrictMode>);
  const article = screen.getByRole('article');
  expect(article.querySelector('#application-storage')).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: 'Open Agent World architecture' }));
  fireEvent.click(screen.getByRole('link', { name: 'Configuration' }));
  expect(screen.getByRole('heading', { name: 'Application storage' }).id).toBe('application-storage');
  expect(document.activeElement?.id).toBe('application-storage');
  fireEvent.click(screen.getByRole('button', { name: 'Your AI team, on one canvas.' }));
  const link = screen.getByRole('link', { name: /GitHub Releases/ });
  expect(link.getAttribute('title')).toBe('Opens online (internet required)');
  expect(link.getAttribute('target')).toBe('_blank');
});
