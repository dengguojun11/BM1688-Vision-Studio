export type BatchAutoLabelSampleState = 'NEW' | 'INCOMPLETE' | 'DONE';

export const DEFAULT_BATCH_AUTO_LABEL_STATES: readonly BatchAutoLabelSampleState[] = [
  'NEW',
  'INCOMPLETE',
  'DONE',
];

const STORAGE_KEY = 'ai_batch_auto_label_sample_states';
const STORAGE_VERSION_KEY = 'ai_batch_auto_label_sample_states_version';
const STORAGE_VERSION = '2';
const VALID_STATES = new Set<BatchAutoLabelSampleState>(['NEW', 'INCOMPLETE', 'DONE']);

type BatchAutoLabelStateStorage = Pick<Storage, 'getItem' | 'setItem'>;

function getStorage(storage?: BatchAutoLabelStateStorage): BatchAutoLabelStateStorage | undefined {
  if (storage) {
    return storage;
  }
  if (typeof window === 'undefined') {
    return undefined;
  }
  return window.localStorage;
}

function parseStates(raw: string | null): BatchAutoLabelSampleState[] {
  if (!raw) {
    return [];
  }
  return raw
    .split(',')
    .map((item) => item.trim())
    .filter((item): item is BatchAutoLabelSampleState => VALID_STATES.has(item as BatchAutoLabelSampleState));
}

/**
 * Read the persisted batch scope while migrating the old implicit NEW-only default.
 * A versioned value is always treated as an explicit user choice, so selecting a
 * subset in the modal remains durable after the default changes.
 */
export function readBatchAutoLabelStates(storage?: BatchAutoLabelStateStorage): BatchAutoLabelSampleState[] {
  const target = getStorage(storage);
  if (!target) {
    return [...DEFAULT_BATCH_AUTO_LABEL_STATES];
  }

  try {
    const raw = target.getItem(STORAGE_KEY);
    if (raw === null) {
      return [...DEFAULT_BATCH_AUTO_LABEL_STATES];
    }
    const parsed = parseStates(raw);
    const version = target.getItem(STORAGE_VERSION_KEY);
    if (!version && raw?.trim() === 'NEW') {
      writeBatchAutoLabelStates(DEFAULT_BATCH_AUTO_LABEL_STATES, target);
      return [...DEFAULT_BATCH_AUTO_LABEL_STATES];
    }
    if (version === STORAGE_VERSION && raw.trim() === '') {
      return [];
    }
    return parsed.length ? parsed : [...DEFAULT_BATCH_AUTO_LABEL_STATES];
  } catch {
    return [...DEFAULT_BATCH_AUTO_LABEL_STATES];
  }
}

export function writeBatchAutoLabelStates(
  states: readonly BatchAutoLabelSampleState[],
  storage?: BatchAutoLabelStateStorage,
): void {
  const target = getStorage(storage);
  if (!target) {
    return;
  }

  try {
    target.setItem(STORAGE_KEY, states.join(','));
    target.setItem(STORAGE_VERSION_KEY, STORAGE_VERSION);
  } catch {
    // A full or disabled localStorage must not block task navigation.
  }
}
