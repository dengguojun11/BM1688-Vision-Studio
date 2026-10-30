import request from '../request';

export const IMAGE_CLEANING_POLICY_PATH = '/v1/image-cleaning-policy';

export type ImageCleaningPreset = 'simple' | 'standard' | 'strict';

export interface ImageCleaningPolicyPreset {
  threshold: number;
}

export interface ImageCleaningPolicy {
  active_preset: ImageCleaningPreset;
  presets: Record<ImageCleaningPreset, ImageCleaningPolicyPreset>;
  revision: number;
  algorithm_version: string;
  config_backend_label: string;
}

export interface ImageCleaningPolicyUpdate {
  active_preset: ImageCleaningPreset;
  presets: Record<ImageCleaningPreset, ImageCleaningPolicyPreset>;
  expected_revision?: number;
}

export interface ImageCleaningPreview {
  active_preset: ImageCleaningPreset;
  threshold: number;
  policy_revision: number;
  algorithm_version: string;
  total_count: number;
  duplicate_count: number;
  keep_count: number;
  duplicate_attachment_ids: number[];
  keep_attachment_ids: number[];
  /** Attachments that were readable and included in the similarity scan. */
  candidate_attachment_ids?: number[];
  /** Attachments skipped because their database row or physical file was not readable. */
  skipped_attachment_ids?: number[];
  skipped_count?: number;
  pairs: Array<{ keep_id: number; duplicate_id: number; similarity: number }>;
  fingerprint: string;
}

export interface ImageCleaningSampleCommand {
  file_id: number;
  data: Record<string, unknown>;
}

/**
 * The upload endpoint may reuse an attachment row for an exact-content retry.
 * Keep the first queued sample for each physical attachment so the cleaning
 * commit receives a one-to-one attachment/sample list.
 */
export function dedupeAttachmentSamples<T extends { file_id: number }>(samples: T[]): T[] {
  const seen = new Set<number>();
  return samples.filter((sample) => {
    if (seen.has(sample.file_id)) return false;
    seen.add(sample.file_id);
    return true;
  });
}

/**
 * Resolve the attachment set that was actually included in a cleaning preview.
 *
 * Newer backends return this set explicitly.  The fallback keeps the client
 * compatible with older responses where the candidate set is the union of
 * the retained and duplicate ids.  Skipped ids are always excluded.
 */
export function getImageCleaningCandidateAttachmentIds(
  preview: Pick<
    ImageCleaningPreview,
    'candidate_attachment_ids' | 'keep_attachment_ids' | 'duplicate_attachment_ids' | 'skipped_attachment_ids'
  >,
): number[] {
  const explicit = Array.isArray(preview.candidate_attachment_ids)
    ? preview.candidate_attachment_ids
    : [...(preview.keep_attachment_ids ?? []), ...(preview.duplicate_attachment_ids ?? [])];
  const skipped = new Set(
    (preview.skipped_attachment_ids ?? [])
      .map((value) => Number(value))
      .filter((value) => Number.isInteger(value) && value > 0),
  );
  const seen = new Set<number>();
  return explicit.reduce<number[]>((result, rawValue) => {
    const value = Number(rawValue);
    if (Number.isInteger(value) && value > 0 && !skipped.has(value) && !seen.has(value)) {
      result.push(value);
      seen.add(value);
    }
    return result;
  }, []);
}

/**
 * Read the attachment ids that the backend confirms were physically deleted
 * before sample creation failed.  Only ids from the submitted duplicate set
 * are accepted, so an unrelated error payload can never remove queue items.
 */
export function extractImageCleaningDeletedAttachmentIds(error: unknown, allowedIds: Iterable<number>): number[] {
  const allowed = new Set(Array.from(allowedIds, (value) => Number(value)).filter((value) => value > 0));
  if (allowed.size === 0) return [];

  const responseData = (error as { response?: { data?: unknown } } | null)?.response?.data;
  const asRecord = (value: unknown): Record<string, unknown> | undefined => {
    if (!value || typeof value !== 'object' || Array.isArray(value)) return undefined;
    return value as Record<string, unknown>;
  };
  const payload = asRecord(responseData);
  const nestedData = asRecord(payload?.data);
  const detail = asRecord(payload?.detail);
  const rawIds =
    nestedData?.deleted_attachment_ids ?? payload?.deleted_attachment_ids ?? detail?.deleted_attachment_ids;
  if (!Array.isArray(rawIds)) return [];

  const deleted: number[] = [];
  const seen = new Set<number>();
  for (const rawId of rawIds) {
    const id = Number(rawId);
    if (Number.isInteger(id) && id > 0 && allowed.has(id) && !seen.has(id)) {
      deleted.push(id);
      seen.add(id);
    }
  }
  return deleted;
}

export async function getImageCleaningPolicy() {
  return request.get<{ data: ImageCleaningPolicy }>(IMAGE_CLEANING_POLICY_PATH) as unknown as Promise<{
    data: ImageCleaningPolicy;
  }>;
}

export async function updateImageCleaningPolicy(
  policy: ImageCleaningPolicyUpdate,
): Promise<{ data: ImageCleaningPolicy }> {
  return request.patch(IMAGE_CLEANING_POLICY_PATH, policy) as unknown as Promise<{
    data: ImageCleaningPolicy;
  }>;
}

export async function previewImageCleaning(
  taskId: number,
  attachmentIds: number[],
): Promise<{ data: ImageCleaningPreview }> {
  return request.post(
    `/v1/tasks/${taskId}/image-cleaning/preview`,
    {
      attachment_ids: attachmentIds,
    },
    {
      timeout: 10 * 60 * 1000,
    },
  ) as unknown as Promise<{ data: ImageCleaningPreview }>;
}

export async function createSamplesWithImageCleaning(
  taskId: number,
  samples: ImageCleaningSampleCommand[],
  cleaning: {
    attachment_ids: number[];
    policy_revision: number;
    fingerprint: string;
  },
): Promise<{
  data: { ids: number[]; deleted_attachment_ids: number[]; deleted_count: number; kept_count: number };
}> {
  return request.post(
    `/v1/tasks/${taskId}/samples/with-image-cleaning`,
    { samples, cleaning },
    {
      timeout: 10 * 60 * 1000,
    },
  ) as unknown as Promise<{
    data: { ids: number[]; deleted_attachment_ids: number[]; deleted_count: number; kept_count: number };
  }>;
}
