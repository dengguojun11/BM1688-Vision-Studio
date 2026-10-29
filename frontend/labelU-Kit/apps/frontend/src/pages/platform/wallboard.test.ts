import { describe, expect, it } from 'vitest';

import { countActiveTrainingJobs } from './wallboard';
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
