// @vitest-environment jsdom
import { useState } from 'react';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { SelectMenuLayer } from './SelectMenuLayer';

afterEach(cleanup);

function Form({ change = vi.fn() }: { change?: (value: string) => void }) {
  const [value, setValue] = useState('one');
  return <><SelectMenuLayer /><label>Choice<select value={value} onChange={event => {
    change(event.target.value); setValue(event.target.value);
  }}><option value="one">One</option><option value="blocked" disabled>Blocked</option>
    <optgroup label="More"><option value="two">Two</option><option value="three">Three</option></optgroup>
    <option hidden value="hidden">Hidden</option></select></label><button>Outside</button></>;
}

describe('shared select menus', () => {
  it('uses an application list for native fields and preserves React changes and focus', () => {
    const change = vi.fn();
    render(<Form change={change} />);
    const select = screen.getByLabelText('Choice') as HTMLSelectElement;
    const pointer = new MouseEvent('mousedown', { bubbles: true, cancelable: true });
    select.dispatchEvent(pointer);
    expect(pointer.defaultPrevented).toBe(true);
    fireEvent.click(select);
    expect(screen.getByRole('listbox').className).toContain('ui-select-menu');
    expect(select.getAttribute('aria-expanded')).toBe('true');
    expect(document.activeElement).toBe(select);
    expect(screen.getAllByRole('option', { hidden: true }).filter(option => option.tagName === 'DIV')).toHaveLength(4);
    fireEvent.click(screen.getByRole('listbox').querySelector('[data-select-option="2"]')!);
    expect(select.value).toBe('two');
    expect(change).toHaveBeenCalledTimes(1);
    expect(change).toHaveBeenCalledWith('two');
    expect(screen.queryByRole('listbox')).toBeNull();
    expect(document.activeElement).toBe(select);
    expect(select.hasAttribute('aria-expanded')).toBe(false);
  });

  it('navigates past disabled options, searches labels and commits with Enter', () => {
    const change = vi.fn();
    render(<Form change={change} />);
    const select = screen.getByLabelText('Choice') as HTMLSelectElement;
    fireEvent.keyDown(select, { key: 'ArrowDown' });
    fireEvent.keyDown(select, { key: 'ArrowDown' });
    expect(document.getElementById(select.getAttribute('aria-activedescendant')!)?.textContent).toBe('Two');
    expect(select.value).toBe('one');
    fireEvent.keyDown(select, { key: 't' });
    fireEvent.keyDown(select, { key: 'h' });
    expect(document.getElementById(select.getAttribute('aria-activedescendant')!)?.textContent).toBe('Three');
    fireEvent.keyDown(select, { key: 'Enter' });
    expect(select.value).toBe('three');
    expect(change).toHaveBeenCalledTimes(1);
    expect(change).toHaveBeenCalledWith('three');
  });

  it.each(['Escape', 'Tab'])('cancels with %s without changing the form', key => {
    const change = vi.fn();
    render(<Form change={change} />);
    const select = screen.getByLabelText('Choice');
    fireEvent.click(select);
    fireEvent.keyDown(select, { key: 'End' });
    fireEvent.keyDown(select, { key });
    expect(screen.queryByRole('listbox')).toBeNull();
    expect(change).not.toHaveBeenCalled();
  });

  it('ignores disabled options and dismisses on outside click or a focus change', () => {
    render(<Form />);
    const select = screen.getByLabelText('Choice');
    fireEvent.click(select);
    fireEvent.click(screen.getByRole('listbox').querySelector('[data-select-option="1"]')!);
    expect(screen.getByRole('listbox')).toBeTruthy();
    fireEvent.mouseDown(screen.getByRole('button', { name: 'Outside' }));
    expect(screen.queryByRole('listbox')).toBeNull();
    fireEvent.click(select);
    fireEvent.focusIn(screen.getByRole('button', { name: 'Outside' }));
    expect(screen.queryByRole('listbox')).toBeNull();
  });

  it('supports placeholder actions that reset their controlled value', () => {
    const change = vi.fn();
    render(<><SelectMenuLayer /><select aria-label="Preset" value="" onChange={event => change(event.target.value)}>
      <option value="">Choose preset</option><option value="run">Run</option>
    </select></>);
    const select = screen.getByLabelText('Preset') as HTMLSelectElement;
    fireEvent.click(select);
    fireEvent.click(screen.getByRole('listbox').querySelector('[data-select-option="1"]')!);
    expect(change).toHaveBeenCalledTimes(1);
    expect(change).toHaveBeenCalledWith('run');
    expect(select.value).toBe('');
  });

  it('allows native programmatic changes used by forms and browser automation', () => {
    const change = vi.fn();
    render(<Form change={change} />);
    fireEvent.change(screen.getByLabelText('Choice'), { target: { value: 'two' } });
    expect(change).toHaveBeenCalledTimes(1);
    expect(change).toHaveBeenCalledWith('two');
  });

  it('covers dynamically mounted controls and closes when their owner disappears', async () => {
    const { rerender } = render(<SelectMenuLayer />);
    rerender(<><SelectMenuLayer /><select aria-label="Plugin"><option>Dynamic</option></select></>);
    fireEvent.click(screen.getByLabelText('Plugin'));
    expect(screen.getByRole('listbox')).toBeTruthy();
    rerender(<SelectMenuLayer />);
    await waitFor(() => expect(screen.queryByRole('listbox')).toBeNull());
  });

  it('preserves disabled controls, multi-selects and existing accessibility attributes', () => {
    render(<><SelectMenuLayer /><select aria-label="Disabled" disabled><option>One</option></select>
      <select aria-label="Multiple" multiple><option>One</option></select>
      <select aria-label="Owned" aria-expanded="false" aria-controls="existing"><option>One</option></select></>);
    fireEvent.click(screen.getByLabelText('Disabled'));
    fireEvent.click(screen.getByLabelText('Multiple'));
    expect(screen.queryByRole('listbox', { name: 'Disabled' })).toBeNull();
    const select = screen.getByLabelText('Owned');
    fireEvent.click(select);
    fireEvent.keyDown(select, { key: 'Escape' });
    expect(select.getAttribute('aria-expanded')).toBe('false');
    expect(select.getAttribute('aria-controls')).toBe('existing');
  });
});
