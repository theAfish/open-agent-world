// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { worldApi } from '../api/client';
import { useLocale } from '../i18n';
import { tutorial, useTutorialStore } from '../onboarding/controller';
import { useWorldStore } from '../state/worldStore';
import { HelpMenu } from './HelpMenu';
import { DOCS_URL, RELEASES_URL, type HelpDiagnostics } from './helpChecks';

const { setCenter } = vi.hoisted(() => ({ setCenter: vi.fn() }));
vi.mock('@xyflow/react', () => ({ useReactFlow: () => ({ setCenter }) }));

const report: HelpDiagnostics = { checked_at: '2026-09-26T05:00:00Z', card_count: 2, checks: [
  { id: 'backend', status: 'ok', code: 'backend' },
  { id: 'agent', name: 'Research Agent', node_id: 'agent', focus_id: 'team', x: 60000, y: -20000, status: 'warning', code: 'model_configuration' },
  { id: 'sandbox', name: 'My Sandbox', node_id: 'sandbox', status: 'info', code: 'sandbox_stopped' },
] };

beforeEach(() => {
  vi.restoreAllMocks();
  setCenter.mockClear();
  useLocale.setState({ locale: 'en' });
  useTutorialStore.setState({ busy: false });
  useWorldStore.setState({ settingsOpen: false, socketState: 'live' });
  HTMLDialogElement.prototype.showModal = function () { this.setAttribute('open', ''); };
  HTMLDialogElement.prototype.close = function () { this.removeAttribute('open'); };
  vi.spyOn(worldApi, 'getDiagnostics').mockResolvedValue(report);
  vi.spyOn(tutorial, 'replay').mockResolvedValue(undefined);
  vi.stubGlobal('matchMedia', () => ({ matches: true }));
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.useRealTimers(); });

function openMenu() {
  render(<HelpMenu />);
  fireEvent.click(screen.getByRole('button', { name: 'Help' }));
}
async function openStatus() {
  openMenu();
  fireEvent.click(screen.getByRole('menuitem', { name: 'Status check' }));
  await screen.findByText('Research Agent');
}

describe('Help menu', () => {
  it('opens help without replaying the tutorial or checking the backend', () => {
    openMenu();
    expect(screen.getAllByRole('menuitem')).toHaveLength(4);
    expect(tutorial.replay).not.toHaveBeenCalled();
    expect(worldApi.getDiagnostics).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('menuitem', { name: 'Tutorial' }));
    expect(tutorial.replay).toHaveBeenCalledOnce();
    expect(screen.queryByRole('menu')).toBeNull();
  });

  it('keeps documentation and diagnostics available while the tutorial is busy', () => {
    useTutorialStore.setState({ busy: true });
    openMenu();
    expect((screen.getByRole('menuitem', { name: 'Tutorial' }) as HTMLButtonElement).disabled).toBe(true);
    expect(document.activeElement).toBe(screen.getByRole('menuitem', { name: 'Documentation' }));
    fireEvent.keyDown(document.activeElement!, { key: 'ArrowDown' });
    expect(document.activeElement).toBe(screen.getByRole('menuitem', { name: 'Status check' }));
    fireEvent.keyDown(document.activeElement!, { key: 'Escape' });
    expect(screen.queryByRole('menu')).toBeNull();
    expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Help' }));
  });

  it('dismisses on outside click and links to localized docs', () => {
    openMenu();
    expect(screen.getByRole('menuitem', { name: 'Documentation' }).getAttribute('href')).toBe(DOCS_URL);
    act(() => useLocale.setState({ locale: 'zh-CN' }));
    expect(screen.getByRole('menuitem', { name: '文档' }).getAttribute('href')).toBe(`${DOCS_URL}README.zh-CN/`);
    fireEvent.pointerDown(document.body);
    expect(screen.queryByRole('menu')).toBeNull();
  });

  it('explains manual updates without claiming an automatic updater or a version comparison', () => {
    openMenu();
    fireEvent.click(screen.getByRole('menuitem', { name: 'Versions and updates' }));
    expect(screen.getByRole('link', { name: 'Open official releases' }).getAttribute('href')).toBe(RELEASES_URL);
    expect(screen.getByText(/Automatic installation is not available/)).toBeTruthy();
    expect(worldApi.getDiagnostics).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Close help' }));
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Help' }));
  });
});

describe('Status check', () => {
  it('shows actionable issues and unverified notes, with passed checks available on demand', async () => {
    await openStatus();
    expect(screen.getByText('1 items need attention')).toBeTruthy();
    expect(screen.getByText('My Sandbox')).toBeTruthy();
    expect(screen.queryByText('Backend connection')).toBeNull();
    fireEvent.click(screen.getByRole('checkbox', { name: 'Show passed checks' }));
    expect(screen.getByText('Backend connection')).toBeTruthy();
    act(() => useWorldStore.setState({ socketState: 'closed' }));
    expect(screen.getByText('2 items need attention')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Open settings' }));
    expect(useWorldStore.getState().settingsOpen).toBe(true);
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('locates distant nested cards through their container', async () => {
    await openStatus();
    fireEvent.click(screen.getByRole('button', { name: 'Locate on canvas' }));
    expect(setCenter).toHaveBeenCalledWith(60048, -19952, { zoom: .9, duration: 0 });
    expect(useWorldStore.getState().selectedCardIds).toEqual(['team']);
  });

  it('offers retry on a failed request and discards the old report', async () => {
    vi.mocked(worldApi.getDiagnostics).mockRejectedValueOnce(new Error('private backend detail'));
    openMenu();
    fireEvent.click(screen.getByRole('menuitem', { name: 'Status check' }));
    await screen.findByRole('alert');
    expect(screen.queryByText('private backend detail')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Check again' }));
    await screen.findByText('Research Agent');
    expect(screen.queryByRole('alert')).toBeNull();
    expect(worldApi.getDiagnostics).toHaveBeenCalledTimes(2);
  });

  it('aborts outstanding checks when closed and ignores a late reply', async () => {
    let resolve!: (value: HelpDiagnostics) => void;
    vi.mocked(worldApi.getDiagnostics).mockImplementation(() => new Promise(done => { resolve = done; }));
    openMenu();
    fireEvent.click(screen.getByRole('menuitem', { name: 'Status check' }));
    const signal = vi.mocked(worldApi.getDiagnostics).mock.calls[0][0]!;
    fireEvent.click(screen.getByRole('button', { name: 'Close help' }));
    expect(signal.aborted).toBe(true);
    await act(async () => resolve(report));
    expect(screen.queryByText('Research Agent')).toBeNull();
  });

  it('aborts a stalled request after the request deadline', async () => {
    vi.useFakeTimers();
    vi.mocked(worldApi.getDiagnostics).mockImplementation(signal => new Promise((_, reject) => {
      signal!.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')));
    }));
    openMenu();
    fireEvent.click(screen.getByRole('menuitem', { name: 'Status check' }));
    await act(async () => { vi.advanceTimersByTime(15000); });
    expect(screen.getByRole('alert')).toBeTruthy();
    vi.useRealTimers();
    await waitFor(() => expect((screen.getByRole('button', { name: 'Check again' }) as HTMLButtonElement).disabled).toBe(false));
  });
});
