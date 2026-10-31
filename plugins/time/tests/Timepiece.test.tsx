// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { Timepiece, TubeDisplay } from '../frontend';
import { calendarDays, durationText, localDate, timerValue, type TimeValue } from '../frontend/time';
import type { PluginViewProps } from '@oaw/plugin-api';

const value: TimeValue = { time_zone:'Asia/Shanghai', offset_minutes: 480, offset_ms: 0, selected_date: '', duration_ms: 300000,
  elapsed_ms: 0, started_at_ms: null, updated_at_ms: 0, count: 0, laps: [] };
afterEach(() => { cleanup(); vi.useRealTimers(); delete (HTMLElement.prototype as Partial<HTMLElement>).showPopover; });

function setup(kind: string, initial: Partial<TimeValue> = {}) {
  let listener = (_change: {nodeId:string;revision:number}) => {};
  const documentAction = vi.fn().mockResolvedValue({ value: { ...value, ...initial, server_now_ms: Date.now() }, revision: 1 });
  const host = { documentAction, onDocumentChange: vi.fn(fn => { listener = fn; return vi.fn(); }) };
  const result = render(<Timepiece {...{ card: { id: 'time', type: `tools.time.${kind}`, config: {} }, host } as unknown as PluginViewProps} />);
  return { ...result, documentAction, change: () => listener({nodeId:'time',revision:2}) };
}

it('draws eight independent fluorescent tubes with six digit positions and two colon positions', () => {
  const { container } = render(<TubeDisplay text="12:34:56" />);
  expect(container.querySelectorAll('svg.time-tube')).toHaveLength(8);
  expect(container.querySelectorAll('.is-colon')).toHaveLength(2);
  expect(screen.getByRole('img').getAttribute('aria-label')).toBe('12:34:56');
});

it('calculates elapsed time from timestamps after a long suspension and clamps countdown completion', () => {
  const running = {...value, elapsed_ms:1000, started_at_ms:10000};
  expect(timerValue('countdown', running, 999999).milliseconds).toBe(0);
  expect(timerValue('countdown', running, 999999).running).toBe(false);
  expect(timerValue('stopwatch', running, 15000).milliseconds).toBe(6000);
  expect(durationText(1, true)).toBe('00:01');
  expect(durationText(3600000)).toBe('01:00:00');
});

it('lays out leap years and Sunday-start months on a Monday-first calendar', () => {
  expect(calendarDays(2024, 1).filter(day => day.getUTCMonth() === 1)).toHaveLength(29);
  const march = calendarDays(2026, 2);
  expect(march[0].toISOString().slice(0,10)).toBe('2026-02-23');
  expect(march[6].toISOString().slice(0,10)).toBe('2026-03-01');
  expect(calendarDays(42, 1)[10].getUTCFullYear()).toBe(42);
});

it('submits counter operations to the host and reflects remote document updates', async () => {
  const { documentAction, change } = setup('counter');
  await screen.findByLabelText('Current count');
  documentAction.mockResolvedValueOnce({ value: {...value,count:1}, revision:2 });
  fireEvent.click(screen.getByRole('button', {name:'Increment count'}));
  await waitFor(() => expect(screen.getByLabelText('Current count').textContent).toBe('1'));
  expect(documentAction).toHaveBeenLastCalledWith('increment', {}, 1);
  documentAction.mockResolvedValue({value:{...value,count:42},revision:3});
  act(change);
  await waitFor(() => expect(screen.getByLabelText('Current count').textContent).toBe('42'));
});

it('shows action failures without claiming success and can reload the current revision', async () => {
  const { documentAction } = setup('counter', {count:7});
  await screen.findByLabelText('Current count');
  documentAction.mockRejectedValueOnce(new Error('The board changed. Reload and retry your change.'));
  fireEvent.click(screen.getByRole('button', {name:'Increment count'}));
  expect((await screen.findByRole('alert')).textContent).toContain('The board changed');
  expect(screen.getByLabelText('Current count').textContent).toBe('7');
  documentAction.mockResolvedValue({value:{...value,count:8},revision:2});
  fireEvent.click(screen.getByRole('button', {name:'Reload'}));
  await waitFor(() => expect(screen.queryByRole('alert')).toBeNull());
  expect(screen.getByLabelText('Current count').textContent).toBe('8');
});

it('advances an active timer locally, displays completion and cleans up its interval', async () => {
  vi.useFakeTimers(); vi.setSystemTime(10000);
  const { unmount, documentAction } = setup('countdown', {duration_ms:1000,started_at_ms:10000});
  await act(async () => {});
  expect(screen.getByLabelText('Timer value').textContent).toBe('00:01');
  act(() => vi.advanceTimersByTime(1250));
  expect(screen.getByText('Complete').getAttribute('role')).toBe('status');
  expect(screen.getByLabelText('Timer value').textContent).toBe('00:00');
  expect(documentAction).toHaveBeenCalledTimes(1);
  unmount(); expect(vi.getTimerCount()).toBe(0);
});

it('changes calendar months without writing, then saves a selected date', async () => {
  const { documentAction } = setup('calendar', {selected_date:'2024-02-29'});
  await screen.findByRole('button', {name:'2024-02-29'});
  fireEvent.click(screen.getByRole('button', {name:'Next month'}));
  expect(documentAction).toHaveBeenCalledTimes(1);
  fireEvent.click(screen.getByRole('button', {name:'2024-03-15'}));
  await waitFor(() => expect(documentAction).toHaveBeenLastCalledWith('set_date', {date:'2024-03-15'}, 1));
});

it('edits duration at the readout and applies hours, minutes and seconds as one host operation', async () => {
  const {documentAction}=setup('countdown');
  await screen.findByLabelText('Timer value');
  fireEvent.click(screen.getByRole('button',{name:'Edit duration'}));
  expect(documentAction).toHaveBeenCalledTimes(1);
  fireEvent.change(screen.getByLabelText('Hours'),{target:{value:'1'}});
  fireEvent.change(screen.getByLabelText('Minutes'),{target:{value:'2'}});
  fireEvent.change(screen.getByLabelText('Seconds'),{target:{value:'3'}});
  documentAction.mockResolvedValueOnce({value:{...value,duration_ms:3723000},revision:2});
  fireEvent.click(screen.getByRole('button',{name:'Apply changes'}));
  await waitFor(()=>expect(documentAction).toHaveBeenLastCalledWith('set_duration',{seconds:3723},1));
  await waitFor(()=>expect(screen.queryByLabelText('Minutes')).toBeNull());
  expect(screen.getByLabelText('Timer value').textContent).toBe('01:02:03');
});

it('preserves an edited count when saving fails, and cancels the draft with Escape without writing it',async()=>{
  const {documentAction}=setup('counter',{count:7});
  await screen.findByLabelText('Current count');
  fireEvent.click(screen.getByRole('button',{name:'Edit count'}));
  const input=screen.getByLabelText('Set count') as HTMLInputElement;
  fireEvent.change(input,{target:{value:'42'}});
  documentAction.mockRejectedValueOnce(new Error('Save failed'));
  fireEvent.click(screen.getByRole('button',{name:'Apply changes'}));
  await screen.findByRole('alert');
  expect((screen.getByLabelText('Set count') as HTMLInputElement).value).toBe('42');
  fireEvent.keyDown(input,{key:'Escape'});
  expect(screen.getByLabelText('Current count').textContent).toBe('7');
  expect(documentAction).toHaveBeenCalledTimes(2);
});

it('sends clock edits in city-local time so the backend applies the rules for the edited date', async()=>{
  const {documentAction}=setup('clock');
  await screen.findByRole('button',{name:'Edit time'});
  expect(screen.queryByText('Analog clock')).toBeNull();
  fireEvent.click(screen.getByRole('button',{name:'Edit time'}));
  fireEvent.change(screen.getByLabelText('Date'),{target:{value:'2030-02-03'}});
  for(const [label,part] of [['Hours','9'],['Minutes','7'],['Seconds','8']]) fireEvent.change(screen.getByLabelText(label),{target:{value:part}});
  fireEvent.click(screen.getByRole('button',{name:'Apply changes'}));
  await waitFor(()=>expect(documentAction).toHaveBeenLastCalledWith('set_time',{local_time:'2030-02-03T09:07:08'},1));
});

it('uses named-zone rules on the frontend even when the saved legacy offset belongs to another city',()=>{
  const newYork={...value,time_zone:'America/New_York'};
  expect(localDate(newYork,Date.UTC(2026,0,15,0,30)).toISOString()).toBe('2026-01-14T19:30:00.000Z');
  expect(localDate(newYork,Date.UTC(2026,6,15,0,30)).toISOString()).toBe('2026-07-14T20:30:00.000Z');
});

it('searches cities in either language and saves a geographical time zone from the location chip',async()=>{
  Object.defineProperty(HTMLElement.prototype,'showPopover',{configurable:true,value:vi.fn()});
  const {documentAction}=setup('digital');
  await screen.findByRole('button',{name:'Choose city: Shanghai'});
  expect(screen.queryByLabelText('Sync with system time')).toBeNull();
  fireEvent.click(screen.getByRole('button',{name:'Choose city: Shanghai'}));
  const search=screen.getByLabelText('Search cities or regions');
  expect(document.activeElement).toBe(search);
  fireEvent.change(search,{target:{value:'纽约'}});
  expect(screen.queryByRole('button',{name:'Paris',exact:true})).toBeNull();
  documentAction.mockResolvedValueOnce({value:{...value,time_zone:'America/New_York'},revision:2});
  fireEvent.click(screen.getByRole('button',{name:'New York',exact:true}));
  await waitFor(()=>expect(documentAction).toHaveBeenLastCalledWith('set_zone',{time_zone:'America/New_York'},1));
  await screen.findByRole('button',{name:'Choose city: New York'});
  expect(screen.queryByRole('dialog')).toBeNull();
});

it('uses accessible watch-style play and pause icons with no large start label',async()=>{
  const {documentAction}=setup('stopwatch');
  const start=await screen.findByRole('button',{name:'Start',exact:true});
  expect(start.textContent).toBe('');
  expect(start.querySelector('svg.lucide-play')).not.toBeNull();
  documentAction.mockResolvedValueOnce({value:{...value,started_at_ms:Date.now()},revision:2});
  fireEvent.click(start);
  const pause=await screen.findByRole('button',{name:'Pause',exact:true});
  expect(pause.querySelector('svg.lucide-pause')).not.toBeNull();
  expect(screen.getByRole('button',{name:'Reset timer'}).querySelector('svg')).not.toBeNull();
});
