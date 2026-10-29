import { describe, expect, it } from 'vitest';

import {
  DEFAULT_BATCH_AUTO_LABEL_STATES,
  readBatchAutoLabelStates,
  writeBatchAutoLabelStates,
  type BatchAutoLabelSampleState,
} from './batchAutoLabelStates';

function createStorage(initial: Record<string, string> = {}) {
  const values = new Map(Object.entries(initial));
  return {
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => values.set(key, value),
    value: (key: string) => values.get(key),
  };
}

describe('batch global-label sample scope', () => {
  it('selects every sample state by default so completed samples are included', () => {
    const storage = createStorage();

    expect(readBatchAutoLabelStates(storage)).toEqual(DEFAULT_BATCH_AUTO_LABEL_STATES);
  });

  it('migrates the legacy implicit NEW-only default to all states', () => {
    const storage = createStorage({ ai_batch_auto_label_sample_states: 'NEW' });

    expect(readBatchAutoLabelStates(storage)).toEqual(['NEW', 'INCOMPLETE', 'DONE']);
    expect(storage.value('ai_batch_auto_label_sample_states')).toBe('NEW,INCOMPLETE,DONE');
    expect(storage.value('ai_batch_auto_label_sample_states_version')).toBe('2');
  });

  it('preserves an explicit subset after the user changes the checkboxes', () => {
    const storage = createStorage();
    const selected: BatchAutoLabelSampleState[] = ['DONE'];

    writeBatchAutoLabelStates(selected, storage);

    expect(readBatchAutoLabelStates(storage)).toEqual(selected);
    expect(storage.value('ai_batch_auto_label_sample_states_version')).toBe('2');
  });

  it('preserves a new explicit NEW-only choice instead of treating it as the legacy default', () => {
    const storage = createStorage();

    writeBatchAutoLabelStates(['NEW'], storage);

    expect(readBatchAutoLabelStates(storage)).toEqual(['NEW']);
  });

  it('preserves an explicit empty selection so the existing validation can warn before starting', () => {
    const storage = createStorage();

    writeBatchAutoLabelStates([], storage);

    expect(readBatchAutoLabelStates(storage)).toEqual([]);
  });

  it('falls back to all states when persisted values are invalid', () => {
    expect(readBatchAutoLabelStates(createStorage({ ai_batch_auto_label_sample_states: 'UNKNOWN' }))).toEqual(
      DEFAULT_BATCH_AUTO_LABEL_STATES,
    );
  });
});
