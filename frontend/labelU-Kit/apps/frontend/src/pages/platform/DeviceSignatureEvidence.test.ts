import { describe, expect, it } from 'vitest';

import { deviceSignatureEvidenceState, truncateEvidence } from './DeviceSignatureEvidence';
import { ruleApiPaths } from './ruleContract';

describe('device signature evidence', () => {
  it('uses the project-scoped device signature report endpoint', () => {
    expect(ruleApiPaths(17).deviceSignatureReports).toBe(
      '/v1/rule-templates/device-signature-reports?project_id=17',
    );
  });

  it('truncates report identifiers and payload hashes without losing the source value', () => {
    expect(truncateEvidence('report-device-0001', 8)).toBe('report-d...');
    expect(truncateEvidence('abcd', 8)).toBe('abcd');
    expect(truncateEvidence(null, 8)).toBe('-');
  });

  it.each([
    [{ loading: true, rowCount: 0 }, 'loading'],
    [{ loading: false, errorStatus: 403, rowCount: 0 }, 'forbidden'],
    [{ loading: false, errorStatus: 500, rowCount: 0 }, 'error'],
    [{ loading: false, rowCount: 0 }, 'empty'],
    [{ loading: false, rowCount: 2 }, 'ready'],
  ] as const)('derives the evidence workspace state', (input, expected) => {
    expect(deviceSignatureEvidenceState(input)).toBe(expected);
  });
});
