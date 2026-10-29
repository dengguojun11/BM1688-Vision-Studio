import { describe, expect, it } from 'vitest';

import { isGlobalToolCompleted } from '../../../../packages/image-annotator-react/src/AttributePanel/globalCompletion';

describe('isGlobalToolCompleted', () => {
  const tagAttributes = [{ value: 'sceneType' }, { value: 'lighting' }];

  it('does not mark an empty configured tag tool as completed', () => {
    expect(isGlobalToolCompleted(tagAttributes, [], {})).toBe(false);
  });

  it('requires every configured tag dimension', () => {
    expect(
      isGlobalToolCompleted(tagAttributes, [], {
        sceneType: { value: { sceneType: 'road' } },
      }),
    ).toBe(false);

    expect(
      isGlobalToolCompleted(tagAttributes, [], {
        sceneType: { value: { sceneType: 'road' } },
        lighting: { value: { lighting: 'daylight' } },
      }),
    ).toBe(true);
  });
});
