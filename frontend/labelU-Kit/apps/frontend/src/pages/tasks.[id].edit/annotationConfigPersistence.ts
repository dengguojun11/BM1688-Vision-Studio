import type { FormInstance } from 'antd';

export function buildPersistedAnnotationConfig(config: Record<string, any> | undefined) {
  const persistedConfig = { ...(config ?? {}) };
  delete persistedConfig.media_type;
  delete persistedConfig.rectLabelPresets;
  delete persistedConfig.selectedRectPresetId;
  return persistedConfig;
}

export function readPersistedAnnotationConfig(form: Pick<FormInstance, 'getFieldsValue'>) {
  return buildPersistedAnnotationConfig(form.getFieldsValue(true));
}
