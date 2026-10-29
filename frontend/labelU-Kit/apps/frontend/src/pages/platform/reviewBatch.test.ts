import { describe, expect, it } from 'vitest';

import {
  buildAnnotationCanvasPath,
  canBatchTransition,
  isReviewRecordInProject,
  summarizeBatchResults,
} from './reviewBatch';

const rows = [
  { id: 1, review_status: 'PENDING' },
  { id: 2, review_status: 'IN_REVIEW' },
  { id: 3, review_status: 'APPROVED' },
];

describe('annotation review batch contract', () => {
  it('allows entering review only for an all-pending selection', () => {
    expect(canBatchTransition(rows, [1], 'IN_REVIEW')).toBe(true);
    expect(canBatchTransition(rows, [1, 2], 'IN_REVIEW')).toBe(false);
    expect(canBatchTransition(rows, [], 'IN_REVIEW')).toBe(false);
  });

  it('allows approve or reject only for an all-in-review selection', () => {
    expect(canBatchTransition(rows, [2], 'APPROVED')).toBe(true);
    expect(canBatchTransition(rows, [2], 'REJECTED')).toBe(true);
    expect(canBatchTransition(rows, [1], 'APPROVED')).toBe(false);
    expect(canBatchTransition(rows, [2, 3], 'REJECTED')).toBe(false);
  });

  it('matches numeric and string ids and rejects ids outside the current page', () => {
    expect(canBatchTransition(rows, ['1'], 'IN_REVIEW')).toBe(true);
    expect(canBatchTransition(rows, [99], 'IN_REVIEW')).toBe(false);
  });

  it('summarizes partial failures and preserves failed ids for retry', () => {
    expect(
      summarizeBatchResults([
        { id: 1, ok: true },
        { id: 2, ok: false, error: new Error('conflict') },
        { id: 3, ok: false },
      ]),
    ).toEqual({ total: 3, succeeded: 1, failed: 2, failedIds: [2, 3] });
  });

  it('builds the existing canvas route only for a matching project and valid ids', () => {
    expect(isReviewRecordInProject(7, 7)).toBe(true);
    expect(isReviewRecordInProject('7', 8)).toBe(false);
    expect(buildAnnotationCanvasPath(42, '105', 7, 7)).toBe('/tasks/42/samples/105?project_id=7');
    expect(buildAnnotationCanvasPath(0, 105, 7, 7)).toBeNull();
    expect(buildAnnotationCanvasPath(42, 105, 8, 7)).toBeNull();
    expect(buildAnnotationCanvasPath(42, 105)).toBe('/tasks/42/samples/105');
  });
});
