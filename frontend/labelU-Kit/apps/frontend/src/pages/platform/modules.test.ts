import { describe, expect, it } from 'vitest';

import { ADVANCED_PLATFORM_MODULES, getActiveModule, INTERNAL_PLATFORM_MODULES, MODULE_COPY, PLATFORM_MODULES } from './modules';

describe('7GAI platform navigation', () => {
  it('exposes training as a first-class module', () => {
    expect(PLATFORM_MODULES.map((module) => module.key)).toContain('training');
    expect(PLATFORM_MODULES.filter((module) => module.key === 'training')).toHaveLength(1);
    expect(PLATFORM_MODULES).toHaveLength(6);
    expect(PLATFORM_MODULES.find((module) => module.key === 'training')?.label).toBe('训练中心');
    expect(PLATFORM_MODULES.find((module) => module.key === 'models')?.label).toBe('模型中心');
    expect(PLATFORM_MODULES.find((module) => module.key === 'evolution')?.label).toBe('进化中心');
    expect(MODULE_COPY.evolution.title).toBe('进化中心');
    expect(PLATFORM_MODULES.map((module) => module.key)).not.toContain('deployments');
    expect(PLATFORM_MODULES.map((module) => module.key)).not.toContain('workflows');
  });

  it('keeps dataset management as an internal governance surface', () => {
    expect(PLATFORM_MODULES.map((module) => module.key)).not.toContain('datasets');
    expect(INTERNAL_PLATFORM_MODULES.find((module) => module.key === 'datasets')?.label).toBe('数据集管理');
    expect(getActiveModule('/datasets')).toBe('datasets');
    expect(MODULE_COPY.datasets.title).toBe('数据集管理');
  });

  it('exposes the legacy annotation task surface inside the 7GAI shell', () => {
    expect(PLATFORM_MODULES.map((module) => module.key)).toContain('tasks');
    expect(getActiveModule('/tasks')).toBe('tasks');
    expect(MODULE_COPY.tasks.description).toContain('任务创建者和协作关系');
  });

  it('keeps technical governance surfaces out of the default navigation', () => {
    const visible = PLATFORM_MODULES.map((module) => module.key);
    expect(visible).not.toContain('datasets');
    expect(visible).not.toContain('ingestion');
    expect(visible).not.toContain('annotation-review');
    expect(INTERNAL_PLATFORM_MODULES.map((module) => module.key)).toEqual([
      'datasets',
      'ingestion',
      'annotation-review',
    ]);
    expect(ADVANCED_PLATFORM_MODULES.map((module) => module.key)).toEqual([
      'algorithms',
      'rules',
      'providers',
    ]);
  });

  it('keeps the training route highlighted independently from algorithms', () => {
    expect(getActiveModule('/training')).toBe('training');
    expect(getActiveModule('/training?project_id=1')).toBe('training');
    expect(getActiveModule('/algorithms')).toBe('algorithms');
  });

  it('describes the current platform capabilities without stale API-integration placeholders', () => {
    const nextCopy = Object.values(MODULE_COPY).map(({ next }) => next);
    expect(nextCopy.join('\n')).not.toMatch(/(?:API|接口|摘要接口)接入后/);
    expect(nextCopy).toContain('数据集供任务和训练复用；标签契约与版本治理仅在系统管理中维护。');
    expect(nextCopy.join('\n')).not.toMatch(/灰度发布|回滚/);
  });
});
