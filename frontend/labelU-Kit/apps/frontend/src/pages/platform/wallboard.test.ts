import { describe, expect, it } from 'vitest';

import { countActiveTrainingJobs, presentWorkbenchAlert, workTypeLabel } from './wallboard';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

describe('countActiveTrainingJobs', () => {
  it('does not count pending workflows as training jobs', () => {
    expect(countActiveTrainingJobs([
      { id: 'workflow:1', job_type: 'expert_feedback', status: 'PENDING' },
      { id: 'deployment:1', job_type: 'DEPLOYMENT', status: 'RUNNING' },
    ])).toBe(0);
  });

  it('counts active supported training job types', () => {
    expect(countActiveTrainingJobs([
      { id: 'train:1', job_type: 'yolo_detection', status: 'PROCESSING' },
      { id: 'train:2', job_type: 'plate_recognition', status: 'QUEUED' },
      { id: 'train:3', job_type: 'yolo_detection', status: 'COMPLETED' },
    ])).toBe(2);
  });

  it('keeps the main workbench copy global instead of project-scoped', () => {
    const source = readFileSync(
      resolve(process.cwd(), 'src/pages/platform/wallboard.tsx'),
      'utf8',
    );
    expect(source).not.toContain('当前项目');
    expect(source).not.toContain('项目运行摘要');
  });
});

describe('presentWorkbenchAlert', () => {
  it('turns a raw edge permission error into an actionable Chinese summary', () => {
    const result = presentWorkbenchAlert({
      type: 'EDGE_VALIDATION',
      message: "[Errno 13] Permission denied: 'yolov11n_qtable'",
    });

    expect(result.summary).toBe('边端真机验证失败');
    expect(result.detail).toContain('目录读写权限');
    expect(result.actionPath).toBe('/edge-task-center');
    expect(result.rawMessage).toContain('yolov11n_qtable');
  });

  it('routes compile failures to the model center without losing diagnostics', () => {
    const result = presentWorkbenchAlert({ type: 'BM1688_COMPILE', message: 'compiler exited with code 1' });

    expect(result.actionPath).toBe('/models');
    expect(result.actionLabel).toBe('查看模型中心');
    expect(result.rawMessage).toBe('compiler exited with code 1');
  });
});

describe('workTypeLabel', () => {
  it('normalizes plate training job types from the API', () => {
    expect(workTypeLabel('plate_detection')).toBe('车牌定位训练（车牌框 + 四角点）');
    expect(workTypeLabel('PLATE_RECOGNITION_V1')).toBe('历史复合车牌训练（兼容）');
  });
});
