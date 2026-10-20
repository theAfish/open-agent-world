// @vitest-environment jsdom
import { createContext, useCallback, useContext, useEffect, useLayoutEffect, useRef, useState, type ComponentProps } from 'react';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { WorkspaceSection, WorkspaceSectionProvider, useWorkspaceSections, type WorkspaceSectionRegistration } from './WorkspaceSection';
import { WorkspaceAccess } from './WorkspaceAccess';

afterEach(cleanup);

const empty = new Set<string>();
const defaults = {
  cardId: 'card-one', editing: false, detachedSectionIds: empty, hiddenSectionIds: empty,
  register: () => () => {}, onSelect: () => {}, onDragStart: () => {}, onHide: () => {},
};
const OwnerContext = createContext('missing');

describe('workspace sections', () => {
  it('reuses ordinary section IDs for a whole-document publication', () => {
    function Probe() {
      const { isInline, isDetached } = useWorkspaceSections();
      return <output>{JSON.stringify([isInline('form'), isDetached('results'), isInline('hidden')])}</output>;
    }
    render(<WorkspaceAccess.Provider value={{ deployed: true, permissions: { 'card-one': [] }, plugin_access: {
      'card-one': { config_fields: [], document_fields: ['text'], summary_fields: [], document_actions: ['save'],
        downloads: [], resource_actions: {}, execution: false, shared_sections: true },
    } }}><WorkspaceSectionProvider {...defaults} detachedSectionIds={new Set(['results'])} hiddenSectionIds={new Set(['hidden'])}>
      <Probe /><WorkspaceSection id="form" title="Form"><input aria-label="Published form" /></WorkspaceSection>
    </WorkspaceSectionProvider></WorkspaceAccess.Provider>);
    expect(screen.getByRole('status').textContent).toBe('[true,true,false]');
    expect(screen.getByLabelText('Published form')).toBeTruthy();
  });
  it('reports detached presentation only for visible, permitted sections', () => {
    function Probe() {
      const sections = useWorkspaceSections();
      return <output>{JSON.stringify({ inline: sections.isInline('files'), detached: sections.isDetached('files') })}</output>;
    }
    const content = (allowed: boolean, hidden: boolean) => <WorkspaceAccess.Provider value={{ deployed: true,
      permissions: allowed ? { 'card-one': ['files'] } : {}, plugin_access: {
        'card-one': { config_fields: [], document_fields: [], summary_fields: [], document_actions: [], downloads: [], resource_actions: {}, execution: false },
      } }}><WorkspaceSectionProvider {...defaults} detachedSectionIds={new Set(['files'])}
        hiddenSectionIds={hidden ? new Set(['files']) : empty}><Probe /></WorkspaceSectionProvider></WorkspaceAccess.Provider>;
    const view = render(content(false, false));
    expect(screen.getByRole('status').textContent).toBe('{"inline":false,"detached":false}');
    view.rerender(content(true, false));
    expect(screen.getByRole('status').textContent).toBe('{"inline":false,"detached":true}');
    view.rerender(content(true, true));
    expect(screen.getByRole('status').textContent).toBe('{"inline":false,"detached":false}');
  });

  it('does not advertise denied plugin content as inline when permissions are missing', () => {
    function Probe() {
      const { isInline, isDetached } = useWorkspaceSections();
      return <output>{String(isInline('files') || isDetached('files'))}</output>;
    }
    render(<WorkspaceAccess.Provider value={{ deployed: true, permissions: {}, plugin_access: {
      'card-one': { config_fields: [], document_fields: [], summary_fields: [], document_actions: [], downloads: [], resource_actions: {}, execution: false },
    } }}><WorkspaceSectionProvider {...defaults}><Probe /></WorkspaceSectionProvider></WorkspaceAccess.Provider>);
    expect(screen.getByRole('status').textContent).toBe('false');
  });

  it('registers optional sizing without replacing its host on owner updates', () => {
    const register = vi.fn(() => () => {});
    const content = (width: number) => <WorkspaceSectionProvider {...defaults} register={register}>
      <WorkspaceSection id="files" title="Files" layout={{ min_width: width, ideal_width: 640 }}><input aria-label="Sizing draft" /></WorkspaceSection>
    </WorkspaceSectionProvider>;
    const view = render(content(280));
    const input = screen.getByLabelText('Sizing draft');
    view.rerender(content(280));
    expect(register).toHaveBeenCalledTimes(1);
    expect(register).toHaveBeenLastCalledWith(expect.objectContaining({ layout: expect.objectContaining({ min_width: 280, ideal_width: 640 }) }));
    view.rerender(content(320));
    expect(register).toHaveBeenCalledTimes(2);
    expect(screen.getByLabelText('Sizing draft')).toBe(input);
  });
  it('does not mount undeclared or hidden plugin sections in a deployment', () => {
    const mounted = vi.fn();
    function PrivateContent() { useEffect(mounted, []); return <p>Private controls</p>; }
    render(<WorkspaceAccess.Provider value={{ deployed: true, permissions: { 'card-one': ['public'] }, plugin_access: {
      'card-one': { config_fields: [], document_fields: [], summary_fields: [], document_actions: [], downloads: [], resource_actions: {}, execution: false },
    } }}><WorkspaceSectionProvider {...defaults}>
      <WorkspaceSection id="public" title="Public"><p>Public controls</p></WorkspaceSection>
      <WorkspaceSection id="private" title="Private"><PrivateContent /></WorkspaceSection>
    </WorkspaceSectionProvider></WorkspaceAccess.Provider>);
    expect(screen.getByText('Public controls')).toBeTruthy();
    expect(screen.queryByText('Private controls')).toBeNull();
    expect(mounted).not.toHaveBeenCalled();
  });
  it('uses the ordinary card layout outside a Legion and connects content before layout measurement', () => {
    const connected = vi.fn();
    function Pane() {
      const element = useRef<HTMLDivElement>(null);
      const { isInline } = useWorkspaceSections();
      useLayoutEffect(() => { connected(element.current?.isConnected); }, []);
      return <div ref={element}>{isInline('files') ? 'Inline files' : 'Detached files'}</div>;
    }
    const view = render(<WorkspaceSection id="files" title="Files" className="file-pane" style={{ flexGrow: 2 }}><Pane /></WorkspaceSection>);
    const shell = view.container.querySelector<HTMLDivElement>('[data-workspace-section="files"]')!;
    expect(shell.classList.contains('file-pane')).toBe(true);
    expect(shell.style.flexGrow).toBe('2');
    expect(screen.getByText('Inline files')).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Arrange Files' })).toBeNull();
    expect(connected).toHaveBeenCalledWith(true);
  });

  it('preserves child state, owner context and shared card state across detach, hide and return', () => {
    let registered!: WorkspaceSectionRegistration;
    const mounted = vi.fn();
    const unmounted = vi.fn();
    const unregister = vi.fn();
    const register = vi.fn((section: WorkspaceSectionRegistration) => { registered = section; return unregister; });
    const ownerClick = vi.fn();
    const destination = document.createElement('div');
    document.body.append(destination);

    function Pane({ onPick }: { onPick: () => void }) {
      const context = useContext(OwnerContext);
      const [count, setCount] = useState(0);
      useEffect(() => { mounted(); return unmounted; }, []);
      return <><input aria-label="Pane draft" defaultValue="" /><button onClick={() => { setCount(value => value + 1); onPick(); }}>{context}: {count}</button></>;
    }
    function Card() {
      const [picks, setPicks] = useState(0);
      const { isInline } = useWorkspaceSections();
      return <div onClick={ownerClick}>
        <p>Shared picks: {picks}</p><p>{isInline('files') ? 'Files in card' : 'Files outside card'}</p>
        <WorkspaceSection id="files" title="Files"><Pane onPick={() => setPicks(value => value + 1)} /></WorkspaceSection>
      </div>;
    }
    function Workspace({ position, editing }: { position: 'inline' | 'detached' | 'hidden'; editing: boolean }) {
      return <OwnerContext.Provider value="owner">
        <WorkspaceSectionProvider {...defaults} register={register} editing={editing}
          detachedSectionIds={position === 'detached' ? new Set(['files']) : empty}
          hiddenSectionIds={position === 'hidden' ? new Set(['files']) : empty}>
          <Card />
        </WorkspaceSectionProvider>
      </OwnerContext.Provider>;
    }
    const view = render(<Workspace position="inline" editing={false} />);
    const originalHost = registered.host;
    const input = screen.getByLabelText<HTMLInputElement>('Pane draft');
    fireEvent.change(input, { target: { value: 'Unsaved work' } });
    fireEvent.click(screen.getByRole('button', { name: 'owner: 0' }));

    view.rerender(<Workspace position="inline" editing />);
    view.rerender(<Workspace position="detached" editing />);
    destination.append(registered.host);
    expect(view.container.querySelector('[data-workspace-section="files"]')).toBeNull();
    expect(screen.getByText('Files outside card')).toBeTruthy();
    expect(screen.getByLabelText('Pane draft')).toBe(input);
    expect(input.value).toBe('Unsaved work');
    fireEvent.click(screen.getByRole('button', { name: 'owner: 1' }));
    expect(screen.getByText('Shared picks: 2')).toBeTruthy();
    expect(ownerClick).toHaveBeenCalledTimes(2);

    view.rerender(<Workspace position="hidden" editing />);
    registered.host.remove();
    expect(screen.queryByLabelText('Pane draft')).toBeNull();
    expect(unmounted).not.toHaveBeenCalled();

    view.rerender(<Workspace position="inline" editing={false} />);
    expect(screen.getByLabelText('Pane draft')).toBe(input);
    expect(screen.getByRole('button', { name: 'owner: 2' })).toBeTruthy();
    expect(screen.getByText('Files in card')).toBeTruthy();
    expect(registered.host).toBe(originalHost);
    expect(mounted).toHaveBeenCalledTimes(1);
    expect(register).toHaveBeenCalledTimes(1);
    view.unmount();
    expect(unmounted).toHaveBeenCalledTimes(1);
    expect(unregister).toHaveBeenCalledTimes(1);
    destination.remove();
  });

  it('attaches a persisted detached section before its children measure their destination', () => {
    const measured = vi.fn();
    const unmounted = vi.fn();
    function Content() {
      const element = useRef<HTMLDivElement>(null);
      useLayoutEffect(() => {
        measured(element.current?.isConnected, element.current?.closest('[data-detached-destination]') !== null);
        return unmounted;
      }, []);
      return <div ref={element}>Detached content</div>;
    }
    function Destination({ host }: { host: HTMLDivElement | null }) {
      const mount = useRef<HTMLDivElement>(null);
      useLayoutEffect(() => {
        if (!host) return;
        const target = mount.current!;
        target.append(host);
        return () => { if (host.parentNode === target) host.remove(); };
      }, [host]);
      return <div ref={mount} data-detached-destination />;
    }
    function Workspace({ detached }: { detached: boolean }) {
      const [host, setHost] = useState<HTMLDivElement | null>(null);
      const register = useCallback((section: WorkspaceSectionRegistration) => {
        setHost(section.host);
        return () => setHost(null);
      }, []);
      return <>
        {detached && <Destination host={host} />}
        <WorkspaceSectionProvider {...defaults} register={register} detachedSectionIds={detached ? new Set(['files']) : empty}>
          <WorkspaceSection id="files" title="Files"><Content /></WorkspaceSection>
        </WorkspaceSectionProvider>
      </>;
    }
    const view = render(<Workspace detached />);
    const content = screen.getByText('Detached content');
    expect(measured).toHaveBeenCalledTimes(1);
    expect(measured).toHaveBeenCalledWith(true, true);
    view.rerender(<Workspace detached={false} />);
    expect(screen.getByText('Detached content')).toBe(content);
    expect(measured).toHaveBeenCalledTimes(1);
    expect(unmounted).not.toHaveBeenCalled();
  });

  it('exposes arrange and hide controls only while editing and contains their gestures', () => {
    const onSelect = vi.fn();
    const onHide = vi.fn();
    const onDragStart = vi.fn();
    const outerGesture = vi.fn();
    const outerDragEnd = vi.fn();
    function Workspace({ editing }: { editing: boolean }) {
      return <div onClick={outerGesture} onPointerDown={outerGesture} onDragStart={outerGesture} onDragEnd={outerDragEnd}>
        <WorkspaceSectionProvider {...defaults} editing={editing} onSelect={onSelect} onHide={onHide} onDragStart={onDragStart}>
          <WorkspaceSection id="files" title="Files"><p>File browser</p></WorkspaceSection>
        </WorkspaceSectionProvider>
      </div>;
    }
    const view = render(<Workspace editing={false} />);
    expect(screen.queryByRole('button', { name: 'Arrange Files' })).toBeNull();
    view.rerender(<Workspace editing />);
    const arrange = screen.getByRole('button', { name: 'Arrange Files' });
    fireEvent.pointerDown(arrange);
    fireEvent.click(arrange);
    fireEvent.dragStart(arrange);
    fireEvent.dragEnd(arrange);
    fireEvent.click(screen.getByRole('button', { name: 'Hide Files' }));
    expect(onSelect).toHaveBeenCalledWith('files');
    expect(onHide).toHaveBeenCalledWith('files');
    expect(onDragStart).toHaveBeenCalledWith(expect.anything(), 'files');
    expect(outerGesture).not.toHaveBeenCalled();
    expect(outerDragEnd).toHaveBeenCalledTimes(1);
  });

  it('updates section metadata without remounting its content', () => {
    const unregistered = vi.fn();
    const registered = vi.fn(() => unregistered);
    const mounted = vi.fn();
    function Pane() { useEffect(mounted, []); return <p>File browser</p>; }
    function Workspace({ title }: Pick<ComponentProps<typeof WorkspaceSection>, 'title'>) {
      return <WorkspaceSectionProvider {...defaults} register={registered}>
        <WorkspaceSection id="files" title={title}><Pane /></WorkspaceSection>
      </WorkspaceSectionProvider>;
    }
    const view = render(<Workspace title="Files" />);
    view.rerender(<Workspace title="Project files" />);
    expect(registered).toHaveBeenLastCalledWith(expect.objectContaining({ id: 'files', title: 'Project files' }));
    expect(unregistered).toHaveBeenCalledTimes(1);
    expect(mounted).toHaveBeenCalledTimes(1);
  });
});
