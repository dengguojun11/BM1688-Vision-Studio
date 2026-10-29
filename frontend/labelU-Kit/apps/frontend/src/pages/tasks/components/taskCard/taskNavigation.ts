import type { Navigation } from 'react-router';

export function isTaskNavigationPending(
  navigationState: Navigation['state'],
  destinationPathname: string | undefined,
  taskId: number | undefined,
): boolean {
  if (navigationState === 'idle' || taskId == null) {
    return false;
  }
  return destinationPathname === `/tasks/${taskId}`;
}
