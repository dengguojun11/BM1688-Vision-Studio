import { v4 as uuidv4 } from 'uuid';

/**
 * Create one client-side key for one user initiated create operation.
 * The caller owns the lifetime of the key (normally a component ref), so a
 * network retry can reuse it while a newly opened form gets a new key.
 */
export function createIdempotencyKey(scope: string): string {
  const normalizedScope = scope.trim().replace(/[^a-zA-Z0-9_-]+/g, '-').replace(/^-+|-+$/g, '') || 'create';
  return `labelu-${normalizedScope}-${uuidv4()}`;
}

/**
 * Axios request options for the optional idempotency contract. Returning
 * undefined when no key is supplied preserves all legacy callers.
 */
export function idempotencyRequestConfig(key?: string): { headers: { 'Idempotency-Key': string } } | undefined {
  const normalizedKey = key?.trim();
  return normalizedKey ? { headers: { 'Idempotency-Key': normalizedKey } } : undefined;
}
