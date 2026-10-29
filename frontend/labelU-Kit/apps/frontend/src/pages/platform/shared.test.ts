import { describe, expect, it } from 'vitest';

import { getTrainingFailureGuidance, listData, listTotal, moduleDataState } from './shared';

describe('moduleDataState', () => {
  it.each([
    [{ loading: true, error: undefined, rowCount: 0 }, 'loading'],
    [{ loading: false, error: new Error('forbidden'), rowCount: 0 }, 'error'],
    [{ loading: false, error: undefined, rowCount: 0 }, 'empty'],
    [{ loading: false, error: undefined, rowCount: 3 }, 'ready'],
    [{ loading: false, error: undefined, rowCount: 3, online: false }, 'offline'],
  ])('maps query state %j to %s', (input, expected) => {
    expect(moduleDataState(input)).toBe(expected);
  });
});

describe('resource pagination payloads', () => {
  it('keeps rows and totals when the API client has already unwrapped Axios data', () => {
    const payload = { data: [{ id: 1 }], meta_data: { total: 36 } };
    expect(listData(payload)).toEqual([{ id: 1 }]);
    expect(listTotal(payload)).toBe(36);
  });

  it('accepts Axios-shaped payloads used by isolated page tests', () => {
    const payload = { data: { data: [{ id: 1 }], meta_data: { total: 36 } } };
    expect(listData(payload)).toEqual([{ id: 1 }]);
    expect(listTotal(payload)).toBe(36);
  });
});

describe('training failure guidance', () => {
  it('does not show recovery guidance for healthy or incomplete jobs', () => {
    expect(getTrainingFailureGuidance({ status: 'COMPLETED' })).toBeNull();
    expect(getTrainingFailureGuidance({ status: 'FAILED' })).not.toBeNull();
  });

  it('maps resource failures to actionable, reversible suggestions', () => {
    expect(getTrainingFailureGuidance({ status: 'FAILED', error_message: 'CUDA out of memory' })).toEqual({
      reason: 'CUDA out of memory',
      steps: [
        '降低输入尺寸或批次大小，确认 Worker 有足够显存/内存',
        '使用相同数据集版本重新提交，先用小规模参数验证',
      ],
    });
    expect(getTrainingFailureGuidance({ status: 'FAILED', failure_reason: '容器镜像拉取失败' })?.steps[0]).toContain('镜像');
  });
});
