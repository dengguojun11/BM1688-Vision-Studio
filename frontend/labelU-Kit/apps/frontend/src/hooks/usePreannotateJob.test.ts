import type { TagtoolPreannotateModelResponse, YoloPreannotateModelResponse } from '@/api/types';
import { describe, expect, it } from 'vitest';

import { modelLabel, sortPreannotateModels } from './preannotateModelOptions';
import { aiDetectionImageSize, DEFAULT_INCLUDE_DONE_SAMPLES, getDefaultIncludeDoneSamples } from './usePreannotateJob';

describe('YOLO preannotation model options', () => {
  const models: YoloPreannotateModelResponse[] = [
    { kind: 'trained', id: 1, title: 'middle', recommended_conf: 0.75 },
    { kind: 'trained', id: 2, title: 'high', recommended_conf: 0.95 },
    { kind: 'imported', id: 3, title: 'external', recommended_conf: null },
  ];

  it('sorts evaluated models by confidence and leaves unevaluated models last', () => {
    expect(sortPreannotateModels(models, 'yolo').map((model) => model.id)).toEqual([2, 1, 3]);
  });

  it('shows only the confidence summary after the model name', () => {
    expect(modelLabel(models[0], 'yolo')).toBe('本系统训练 / middle · 置信度 0.75');
    expect(modelLabel(models[2], 'yolo')).toBe('未分类 / external · 置信度 未评估');
  });

  it('keeps tagtool labels and ordering unchanged', () => {
    const tagtool: TagtoolPreannotateModelResponse[] = [
      { kind: 'imported', id: 4, title: 'tag-b' },
      { kind: 'imported', id: 5, title: 'tag-a' },
    ];
    expect(sortPreannotateModels(tagtool, 'tagtool')).toBe(tagtool);
    expect(modelLabel(tagtool[0], 'tagtool')).toBe('未分类 / tag-b');
  });

  it('keeps the server coverage and quality ranking for automatic AI detection', () => {
    const rankedModels: YoloPreannotateModelResponse[] = [
      { ...models[0], matched_labels: ['person', 'car'], missing_task_labels: [] },
      { ...models[1], matched_labels: ['person'], missing_task_labels: ['car'] },
    ];
    expect(sortPreannotateModels(rankedModels, 'ai_detection')).toBe(rankedModels);
  });

  it('uses at least 2560 pixels for AI detection without shrinking larger model inputs', () => {
    expect(aiDetectionImageSize()).toBe(2560);
    expect(aiDetectionImageSize({ ...models[0], imgsz: 640 })).toBe(2560);
    expect(aiDetectionImageSize({ ...models[0], imgsz: 2560 })).toBe(2560);
  });
});

describe('task-level preannotation scope', () => {
  it('includes completed samples by default for the whole-task action', () => {
    expect(DEFAULT_INCLUDE_DONE_SAMPLES).toBe(true);
    expect(getDefaultIncludeDoneSamples('task')).toBe(true);
    expect(getDefaultIncludeDoneSamples('sample')).toBe(false);
  });
});
