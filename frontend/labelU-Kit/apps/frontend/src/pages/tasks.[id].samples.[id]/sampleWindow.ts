type SampleWithId = {
  id?: number;
};

type SampleWithPrefetchData = SampleWithId & {
  file?: {
    filename?: string | null;
    url?: string | null;
  } | null;
};

export function createSampleWindow<T extends SampleWithId>(
  previousSamples: T[],
  currentSample: T,
  nextSamples: T[],
): T[] {
  return deduplicateSamplesById([
    ...previousSamples.filter((sample) => sample.id !== currentSample.id),
    currentSample,
    ...nextSamples.filter((sample) => sample.id !== currentSample.id),
  ]);
}

export function getNextSampleCursor(samples: SampleWithId[]) {
  return samples[samples.length - 1]?.id;
}

export function getPreviousSampleCursor(samples: SampleWithId[]) {
  return samples[0]?.id;
}

export function createCurrentFirstSampleWindow<T extends SampleWithId>(currentSample: T, nextSamples: T[]): T[] {
  return deduplicateSamplesById([
    currentSample,
    ...nextSamples.filter((sample) => sample.id !== currentSample.id),
  ]);
}

export function prependSampleWindow<T extends SampleWithId>(previousSamples: T[], currentSamples: T[]): T[] {
  return deduplicateSamplesById([...previousSamples, ...currentSamples]);
}

export function createNearbySamplePrefetchTargets<T extends SampleWithPrefetchData>(
  samples: T[],
  currentSampleId: number | undefined,
  radius = 2,
): T[] {
  if (typeof currentSampleId !== 'number' || radius <= 0) {
    return [];
  }

  const currentIndex = samples.findIndex((sample) => sample.id === currentSampleId);

  if (currentIndex < 0) {
    return [];
  }

  const start = Math.max(0, currentIndex - radius);
  const end = Math.min(samples.length, currentIndex + radius + 1);
  const seen = new Set<number>();

  return samples.slice(start, end).filter((sample) => {
    if (typeof sample.id !== 'number' || sample.id === currentSampleId || !sample.file?.url) {
      return false;
    }

    if (seen.has(sample.id)) {
      return false;
    }

    seen.add(sample.id);
    return true;
  });
}

export type SampleNavigationSource = 'list' | 'next' | 'prev';

export function shouldPreserveSampleWindowForNavigation(
  source: SampleNavigationSource | undefined,
  nextSampleId: number | undefined,
  initializedSampleId: number | undefined,
) {
  return (
    source === 'list' &&
    typeof nextSampleId === 'number' &&
    typeof initializedSampleId === 'number' &&
    nextSampleId !== initializedSampleId
  );
}

function deduplicateSamplesById<T extends SampleWithId>(samples: T[]): T[] {
  const seen = new Set<number>();

  return samples.filter((sample) => {
    if (typeof sample.id !== 'number') {
      return true;
    }

    if (seen.has(sample.id)) {
      return false;
    }

    seen.add(sample.id);
    return true;
  });
}
