import type { ILabel } from '@labelu/interface';
import { describe, expect, it } from 'vitest';

import { buildToolLabelEntries } from '../../../../packages/image-annotator-react/src/LabelSection/labelAssociations';

const doublePlate = { key: '双层车牌', value: 'doublePlate' } as ILabel;
const platePoint = { key: '关键点', value: 'platePoint' } as ILabel;
const config = {
  rect: { labels: [doublePlate] },
  polygon: { labels: [platePoint] },
};

describe('buildToolLabelEntries', () => {
  it('returns every configured tool label with its annotation count', () => {
    const entries = buildToolLabelEntries(['rect', 'polygon'], config, [
      { tool: 'rect', label: 'doublePlate' },
      { tool: 'rect', label: 'doublePlate' },
      { tool: 'polygon', label: 'platePoint' },
    ]);

    expect(entries).toEqual([
      { toolName: 'rect', label: doublePlate, count: 2 },
      { toolName: 'polygon', label: platePoint, count: 1 },
    ]);
  });

  it('keeps all configured labels visible for an empty sample', () => {
    expect(buildToolLabelEntries(['rect', 'polygon'], config, [])).toEqual([
      { toolName: 'rect', label: doublePlate, count: 0 },
      { toolName: 'polygon', label: platePoint, count: 0 },
    ]);
  });
});
