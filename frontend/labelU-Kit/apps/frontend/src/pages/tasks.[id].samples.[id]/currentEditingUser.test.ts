import { describe, expect, it } from 'vitest';

import type { TaskSampleUser } from '@/hooks/useSampleWs';

import { getCurrentEditingUser } from './currentEditingUser';

describe('getCurrentEditingUser', () => {
  it('falls back to current user when sample locking is disabled', () => {
    expect(getCurrentEditingUser([], { id: 7, username: 'alice' }, '3', '5')).toEqual({
      user_id: 7,
      username: 'alice',
      task_id: 3,
      sample_id: 5,
    });
  });

  it('keeps websocket editor when present', () => {
    const editor: TaskSampleUser = {
      user_id: 9,
      username: 'bob',
      task_id: 3,
      sample_id: 5,
    };

    expect(getCurrentEditingUser([editor], { id: 7, username: 'alice' }, '3', '5')).toBe(editor);
  });
});
