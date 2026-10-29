export function ruleApiPaths(projectId: number, templateId?: number) {
  return {
    templates: `/v1/rule-templates?project_id=${projectId}`,
    versions: templateId ? `/v1/rule-templates/${templateId}/versions` : null,
    bindings: `/v1/rule-templates/bindings?project_id=${projectId}`,
    bindingAudits: `/v1/rule-templates/binding-audits?project_id=${projectId}`,
    versionAudits: `/v1/rule-templates/version-audits?project_id=${projectId}`,
    deviceSignatureReports: `/v1/rule-templates/device-signature-reports?project_id=${projectId}`,
  };
}
