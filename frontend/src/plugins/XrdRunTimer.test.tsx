// @vitest-environment jsdom
import {render, screen, cleanup} from '@testing-library/react';
import {afterEach, expect, it, vi} from 'vitest';
import {act} from 'react';
import {RunTimer} from '../../../plugins/xrd/frontend/RunTimer';
afterEach(()=>{cleanup();vi.useRealTimers();});
it('ticks from saved backend start and freezes at completion',()=>{
 vi.useFakeTimers();vi.setSystemTime(100000);
 const {rerender}=render(<RunTimer timing={{started_at_ms:90000,running:true}}/>);
 expect(screen.getByText('运行中 00:00:10')).toBeTruthy();
 act(()=>vi.advanceTimersByTime(2000));
 expect(screen.getByText('运行中 00:00:12')).toBeTruthy();
 rerender(<RunTimer timing={{started_at_ms:90000,finished_at_ms:102000,running:false}}/>);
 act(()=>vi.advanceTimersByTime(5000));
 expect(screen.getByText('耗时 00:00:12')).toBeTruthy();
});
it('does not invent a duration for older results without finish timestamps',()=>{
 const {container}=render(<RunTimer timing={{started_at_ms:90000,running:false}}/>);
 expect(container.textContent).toBe('');
});
