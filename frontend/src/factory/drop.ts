import { apiErrorMessage, worldApi } from '../api/client';
import { useWorldStore } from '../state/worldStore';
import type { BasketItem } from './types';

export function factoryDropTarget(event: Pick<MouseEvent, 'clientX' | 'clientY'> | TouchEvent, exclude: string[] = []) {
  if (!('clientX' in event)) return undefined;
  for (const element of document.querySelectorAll<HTMLElement>('[data-factory-packer]')) {
    const id = element.dataset.factoryPacker;
    if (!id || exclude.includes(id)) continue;
    const bounds = element.getBoundingClientRect();
    // Clip to the scrolling card surface as well as its visible drop area.
    const surface = element.closest('.factory')?.getBoundingClientRect() ?? bounds;
    if (event.clientX >= Math.max(bounds.left, surface.left) && event.clientX <= Math.min(bounds.right, surface.right)
      && event.clientY >= Math.max(bounds.top, surface.top) && event.clientY <= Math.min(bounds.bottom, surface.bottom)) return id;
  }
}

export async function dropFactoryItems(packerId: string, items: BasketItem[]) {
  try {
    for (const item of items) await worldApi.factory(packerId, 'items', item);
    useWorldStore.getState().pushToast({ tone: 'success', title: '已加入打包器', detail: '导出时复制当前配置，原件保留。' });
  } catch (cause) {
    useWorldStore.getState().pushToast({ tone: 'error', title: '未能加入打包器', detail: apiErrorMessage(cause) });
  } finally { await useWorldStore.getState().refreshWorld(); }
}
