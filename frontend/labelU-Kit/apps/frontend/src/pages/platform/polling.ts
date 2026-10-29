import { useEffect, useState } from 'react';

export const ACTIVE_TRAINING_STATUSES = new Set(['PENDING', 'PROCESSING', 'RUNNING', 'QUEUED']);

export const ACTIVE_RELEASE_STATUSES = new Set([
  'APPROVED',
  'PRECHECKING',
  'STAGED',
  'CANARY',
  'OBSERVING',
  'EXPANDING',
  'RUNNING',
  'DOWNLOADING',
  'ACTIVATING',
]);

export function usePageVisible() {
  const [visible, setVisible] = useState(
    () => typeof document === 'undefined' || document.visibilityState !== 'hidden',
  );

  useEffect(() => {
    const onVisibilityChange = () => setVisible(document.visibilityState !== 'hidden');
    document.addEventListener('visibilitychange', onVisibilityChange);
    return () => document.removeEventListener('visibilitychange', onVisibilityChange);
  }, []);

  return visible;
}
