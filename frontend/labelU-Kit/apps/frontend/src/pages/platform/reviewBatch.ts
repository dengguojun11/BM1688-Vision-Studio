export type ReviewBatchTargetStatus = 'IN_REVIEW' | 'APPROVED' | 'REJECTED';

export type ReviewBatchRow = {
  id?: number | string;
  review_status?: string | null;
};

const EXPECTED_SOURCE_STATUS: Record<ReviewBatchTargetStatus, string> = {
  IN_REVIEW: 'PENDING',
  APPROVED: 'IN_REVIEW',
  REJECTED: 'IN_REVIEW',
};

function sameId(left: number | string, right: number | string) {
  return String(left) === String(right);
}

/**
 * A batch operation must be valid for every selected row. This mirrors the
 * server-side transition graph and keeps mixed-status selections read-only.
 */
export function canBatchTransition(
  rows: ReviewBatchRow[],
  selectedIds: Array<number | string>,
  targetStatus: ReviewBatchTargetStatus,
) {
  if (!selectedIds.length) return false;
  const selectedRows = rows.filter(
    (row) => row.id != null && selectedIds.some((id) => sameId(row.id as number | string, id)),
  );
  if (selectedRows.length !== selectedIds.length) return false;
  const expected = EXPECTED_SOURCE_STATUS[targetStatus];
  return selectedRows.every((row) => String(row.review_status || '').toUpperCase() === expected);
}

export type ReviewBatchResult = { id: number | string; ok: boolean; error?: unknown };

export function summarizeBatchResults(results: ReviewBatchResult[]) {
  const succeeded = results.filter((result) => result.ok).length;
  return {
    total: results.length,
    succeeded,
    failed: results.length - succeeded,
    failedIds: results.filter((result) => !result.ok).map((result) => result.id),
  };
}

function positiveInteger(value: unknown) {
  const parsed = typeof value === 'number' ? value : Number(String(value ?? '').trim());
  return Number.isInteger(parsed) && parsed > 0 ? parsed : null;
}

/** Keep the historical project check when IDs are supplied, but support the
 *一期 global review queue where the backend already authorizes by current user. */
export function isReviewRecordInProject(recordProjectId: unknown, currentProjectId: unknown) {
  const recordId = positiveInteger(recordProjectId);
  const projectId = positiveInteger(currentProjectId);
  return recordId != null && projectId != null && recordId === projectId;
}

/** Build the existing task/sample route without accepting malformed ids. */
export function buildAnnotationCanvasPath(
  taskId: unknown,
  sampleId: unknown,
  projectId?: unknown,
  recordProjectId?: unknown,
) {
  const task = positiveInteger(taskId);
  const sample = positiveInteger(sampleId);
  if (task == null || sample == null) return null;
  if (projectId == null && recordProjectId == null) return `/tasks/${task}/samples/${sample}`;
  if (!isReviewRecordInProject(recordProjectId, projectId)) return null;
  const query = new URLSearchParams({ project_id: String(positiveInteger(projectId)) });
  return `/tasks/${task}/samples/${sample}?${query.toString()}`;
}
