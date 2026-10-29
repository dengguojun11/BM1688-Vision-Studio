import { describe, expect, it } from 'vitest';

import { mergeTrainingAnalysisDatasetOptions, trainingAnalysisDatasetQuery } from './datasetSelector';

describe('training analysis dataset selector', () => {
  it('normalizes the server query and omits blank search terms', () => {
    expect(trainingAnalysisDatasetQuery(3, '  夜间道路  ')).toEqual({
      page: 3,
      size: 20,
      q: '夜间道路',
    });
    expect(trainingAnalysisDatasetQuery(-2, '   ')).toEqual({ page: 0, size: 20 });
  });

  it('keeps a selected dataset visible when it is outside the current page', () => {
    const currentPage = [
      { id: 9, name: '当前页数据集' },
      { id: 10, name: '另一个数据集' },
    ];
    const selected = { id: 3, name: '已选数据集' };

    expect(mergeTrainingAnalysisDatasetOptions(currentPage, selected)).toEqual([...currentPage, selected]);
  });

  it('deduplicates the selected row when it is already on the current page', () => {
    const selected = { id: 9, name: '已选数据集（最新）' };
    expect(mergeTrainingAnalysisDatasetOptions([{ id: 9, name: '旧数据' }], selected)).toEqual([selected]);
  });
});
