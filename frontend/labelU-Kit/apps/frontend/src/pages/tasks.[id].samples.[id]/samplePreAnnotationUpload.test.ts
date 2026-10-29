import { describe, expect, it } from 'vitest';

import { selectCurrentImagePreAnnotationFile } from './samplePreAnnotationUpload';
import { readFile } from '../tasks.[id].edit/partials/InputData/utils';

const record = (fileName: string) => ({
  fileName,
  url: `/${fileName}`,
  result: JSON.stringify({ annotations: [], config: {} }),
});

describe('selectCurrentImagePreAnnotationFile', () => {
  it('filters JSON records by current image filename', async () => {
    const file = new File([JSON.stringify([record('a.jpg'), record('nested/b.jpg')])], 'labels.json');
    const selected = await selectCurrentImagePreAnnotationFile(file, 'b.jpg');
    const output = JSON.parse(await readFile(selected, 'text'));
    expect(output).toHaveLength(1);
    expect(output[0].fileName).toBe('nested/b.jpg');
  });

  it('filters JSONL records by sample_name', async () => {
    const rows = [
      { sample_name: 'a.jpg', annotations: {}, config: {} },
      { sample_name: 'folder/b.jpg', annotations: {}, config: {} },
    ];
    const file = new File([rows.map((row) => JSON.stringify(row)).join('\n')], 'labels.jsonl');
    const selected = await selectCurrentImagePreAnnotationFile(file, 'b.jpg');
    const output = (await readFile(selected, 'text')).trim();
    expect(output.split('\n')).toHaveLength(1);
    expect(JSON.parse(output).sample_name).toBe('folder/b.jpg');
  });

  it('rejects a file without the current image', async () => {
    const file = new File([JSON.stringify([record('a.jpg')])], 'labels.json');
    await expect(selectCurrentImagePreAnnotationFile(file, 'missing.jpg')).rejects.toThrow('未找到当前图片');
  });
});
