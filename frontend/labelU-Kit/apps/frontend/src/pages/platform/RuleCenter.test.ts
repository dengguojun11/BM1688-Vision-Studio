import { describe, expect, it } from 'vitest';

import { ruleApiPaths } from './ruleContract';

describe('rule center API contract', () => {
  it('keeps every collection request inside the selected project', () => {
    expect(ruleApiPaths(17, 23)).toEqual({
      templates: '/v1/rule-templates?project_id=17',
      versions: '/v1/rule-templates/23/versions',
      bindings: '/v1/rule-templates/bindings?project_id=17',
      bindingAudits: '/v1/rule-templates/binding-audits?project_id=17',
      versionAudits: '/v1/rule-templates/version-audits?project_id=17',
      deviceSignatureReports: '/v1/rule-templates/device-signature-reports?project_id=17',
    });
  });

  it('does not create a version endpoint before a template is selected', () => {
    expect(ruleApiPaths(17).versions).toBeNull();
  });
});
