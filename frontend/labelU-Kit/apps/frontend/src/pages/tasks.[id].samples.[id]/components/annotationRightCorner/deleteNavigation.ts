type SampleWithId = {
  id?: number;
};

export type DeleteNavigationTarget = {
  path: string;
  source?: 'next' | 'prev';
};

type ResolveDeleteNavigationArgs = {
  taskId: number | string;
  samples: SampleWithId[];
  sampleIndex: number;
  nextFetchedSampleId?: number;
  previousFetchedSampleId?: number;
};

function buildSamplePath(taskId: number | string, sampleId: number) {
  return `/tasks/${taskId}/samples/${sampleId}`;
}

export function resolveDeleteNavigationTarget({
  taskId,
  samples,
  sampleIndex,
  nextFetchedSampleId,
  previousFetchedSampleId,
}: ResolveDeleteNavigationArgs): DeleteNavigationTarget {
  const nextWindowSampleId = samples[sampleIndex + 1]?.id;
  if (typeof nextWindowSampleId === 'number') {
    return {
      path: buildSamplePath(taskId, nextWindowSampleId),
      source: 'next',
    };
  }

  if (typeof nextFetchedSampleId === 'number') {
    return {
      path: buildSamplePath(taskId, nextFetchedSampleId),
    };
  }

  const previousWindowSampleId = samples[sampleIndex - 1]?.id;
  if (typeof previousWindowSampleId === 'number') {
    return {
      path: buildSamplePath(taskId, previousWindowSampleId),
      source: 'prev',
    };
  }

  if (typeof previousFetchedSampleId === 'number') {
    return {
      path: buildSamplePath(taskId, previousFetchedSampleId),
    };
  }

  return {
    path: `/tasks/${taskId}`,
  };
}
