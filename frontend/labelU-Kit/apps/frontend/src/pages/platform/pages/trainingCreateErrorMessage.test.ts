import { describe, expect, it } from 'vitest';

import { trainingCreateErrorMessage } from './training';

describe('trainingCreateErrorMessage', () => {
  it('prefers the LabelU msg field over a generic 409 fallback', () => {
    expect(
      trainingCreateErrorMessage({
        response: {
          status: 409,
          data: { msg: '相同来源和样本已经生成过数据版本' },
        },
      }),
    ).toBe('相同来源和样本已经生成过数据版本');
  });

  it('accepts detail strings and nested detail messages', () => {
    expect(
      trainingCreateErrorMessage({
        response: { status: 409, data: { detail: '训练快照仍在生成，请稍候' } },
      }),
    ).toBe('训练快照仍在生成，请稍候');
    expect(
      trainingCreateErrorMessage({
        response: { status: 409, data: { detail: { msg: '快照锁冲突' } } },
      }),
    ).toBe('快照锁冲突');
  });

  it('keeps status fallbacks when the server does not provide a message', () => {
    expect(trainingCreateErrorMessage({ response: { status: 409, data: {} } })).toBe(
      '任务或训练资源状态已变化，请刷新候选任务后重试',
    );
    expect(trainingCreateErrorMessage({ response: { status: 422 } })).toBe(
      '训练参数不符合服务端约束，请检查后重试',
    );
    expect(trainingCreateErrorMessage({})).toBe('创建训练作业失败，请稍后重试');
  });
});
