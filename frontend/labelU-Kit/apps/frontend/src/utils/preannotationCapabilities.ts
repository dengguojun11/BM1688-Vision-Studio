import type { PreannotationCapabilityItem } from '@/api/types';

export type PreannotationCapabilityMap = Record<string, PreannotationCapabilityItem>;

/**
 * The capability endpoint has returned both a bare item list and the normal
 * LabelU `{ data: { items } }` envelope during the rollout. Keep the task page
 * tolerant of either shape while using one visibility rule.
 */
export function normalizePreannotationCapabilities(payload: unknown): PreannotationCapabilityMap {
  let value: any = payload;
  if (value && typeof value === 'object' && 'data' in value) value = value.data;

  const rawItems = Array.isArray(value)
    ? value
    : Array.isArray(value?.items)
      ? value.items
      : Array.isArray(value?.capabilities)
        ? value.capabilities
        : value && typeof value === 'object'
          ? Object.entries(value).map(([key, item]) => ({ ...(item as object), key }))
          : [];

  return (rawItems as unknown[]).reduce<PreannotationCapabilityMap>((result: PreannotationCapabilityMap, item: unknown) => {
    if (!item || typeof item !== 'object') return result;
    const key = String((item as any).key || (item as any).provider_key || (item as any).provider || '').trim();
    if (!key) return result;
    result[key] = { ...(item as PreannotationCapabilityItem), key };
    return result;
  }, {});
}
export function isPreannotationCapabilityVisible(item?: PreannotationCapabilityItem): boolean {
  if (!item) return false;
  return item.visible !== false && item.available !== false;
}
