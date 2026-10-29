interface TaskRouteRevalidationArgs {
  currentUrl: URL;
  nextUrl: URL;
  defaultShouldRevalidate: boolean;
}

const taskDetailPathPattern = /^\/tasks\/(\d+)\/?$/;
const taskSampleChildPathPattern = /^\/tasks\/(\d+)\/samples\/(?:\d+|finished)\/?$/;

/**
 * The sample route intentionally skips loading the task's sample list. When
 * returning to the task index, force the shared parent loader to replace that
 * partial loader result with the complete task detail data.
 */
export function shouldRevalidateTaskRoute({
  currentUrl,
  nextUrl,
  defaultShouldRevalidate,
}: TaskRouteRevalidationArgs): boolean {
  const currentTask = currentUrl.pathname.match(taskSampleChildPathPattern);
  const nextTask = nextUrl.pathname.match(taskDetailPathPattern);

  if (currentTask && nextTask && currentTask[1] === nextTask[1]) {
    return true;
  }

  return defaultShouldRevalidate;
}
