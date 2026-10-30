import { describe, expect, it } from 'vitest';

import {
  dedupeAttachmentSamples,
  extractImageCleaningDeletedAttachmentIds,
  getImageCleaningCandidateAttachmentIds,
} from './imageCleaning';

describe('dedupeAttachmentSamples', () => {
  it('keeps one queued sample for a reused physical attachment', () => {
    const first = { file_id: 12, data: { result: '{}' } };
    const second = { file_id: 12, data: { result: '{"retry":true}' } };

    expect(dedupeAttachmentSamples([first, second, { file_id: 13, data: { result: '{}' } }])).toEqual([
      first,
      { file_id: 13, data: { result: '{}' } },
    ]);
  });
});

describe('getImageCleaningCandidateAttachmentIds', () => {
  it('prefers the explicit candidate list and excludes skipped ids', () => {
    expect(
      getImageCleaningCandidateAttachmentIds({
        candidate_attachment_ids: [12, 13, 12, 14],
        keep_attachment_ids: [12],
        duplicate_attachment_ids: [13],
        skipped_attachment_ids: [14],
      }),
    ).toEqual([12, 13]);
  });

  it('derives candidates from kept and duplicate ids for older responses', () => {
    expect(
      getImageCleaningCandidateAttachmentIds({
        keep_attachment_ids: [21],
        duplicate_attachment_ids: [22, 21],
        skipped_attachment_ids: [23],
      }),
    ).toEqual([21, 22]);
  });
});

describe('extractImageCleaningDeletedAttachmentIds', () => {
  it('accepts only backend-confirmed deleted duplicate ids', () => {
    const error = {
      response: {
        data: {
          msg: '样本创建失败',
          data: {
            deleted_attachment_ids: [12, '12', 99, -1],
          },
        },
      },
    };

    expect(extractImageCleaningDeletedAttachmentIds(error, [12, 13])).toEqual([12]);
  });

  it('returns no ids when deletion was not confirmed', () => {
    expect(
      extractImageCleaningDeletedAttachmentIds({ response: { data: { msg: '物理文件删除失败' } } }, [12, 13]),
    ).toEqual([]);
  });

  it('supports the structured detail form without trusting unrelated ids', () => {
    expect(
      extractImageCleaningDeletedAttachmentIds(
        { response: { data: { detail: { deleted_attachment_ids: [13, 14] } } } },
        [12, 13],
      ),
    ).toEqual([13]);
  });
});
