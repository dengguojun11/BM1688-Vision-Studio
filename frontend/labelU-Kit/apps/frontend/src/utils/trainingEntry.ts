export type TrainingEntryTarget = 'plate_detection';

const PLATE_DETECTION_PRESET = 'plateDetection';
const PLATE_RECOGNITION_PRESET = 'plateRecognition';

function parseConfig(raw: unknown): Record<string, any> {
  if (raw && typeof raw === 'object') return raw as Record<string, any>;
  if (typeof raw !== 'string' || !raw.trim()) return {};
  try {
    const parsed = JSON.parse(raw);
    return parsed && typeof parsed === 'object' ? parsed : {};
  } catch {
    return {};
  }
}

function toolAttributeValues(config: Record<string, any>, toolName: string): Set<string> {
  const tool = (config.tools || []).find(
    (item: any) => item && item.tool === toolName,
  );
  const attributes = tool?.config?.attributes;
  if (!Array.isArray(attributes)) return new Set();
  return new Set(
    attributes.flatMap((item: any) => [item?.value, item?.key])
      .map((value: unknown) => String(value || '').trim())
      .filter(Boolean),
  );
}

/** Infer the specialized training target saved by the task annotation preset. */
export function inferTrainingTargetFromTaskConfig(raw: unknown): TrainingEntryTarget | null {
  const config = parseConfig(raw);
  const marker = String(config.training_profile || config.trainingProfile || '').trim();
  const preset = String(config.defaultRectPresetId || '').trim();
  if (marker === 'plate_detection_v1' || preset === PLATE_DETECTION_PRESET) {
    return 'plate_detection';
  }
  if (marker === 'plate_recognition_v1' || preset === PLATE_RECOGNITION_PRESET) {
    // Historical composite annotations use the same rectangle-plus-four-point
    // geometry. New training always routes them to the detection-only Pose
    // contract; historical recognition jobs remain available in job details.
    return 'plate_detection';
  }

  // Some imported historical tasks have no preset marker. Their geometry and
  // attribute keys still identify the same profile without changing the task.
  const rectValues = toolAttributeValues(config, 'rectTool');
  const polygonValues = toolAttributeValues(config, 'polygonTool');
  if (rectValues.has('doubleEP') && polygonValues.has('platePoint')) {
    return 'plate_detection';
  }
  return null;
}

/** Build the task-card deep link without dropping the current task scope. */
export function trainingEntryPath(
  taskId: number,
  datasetId?: number | null,
  taskConfig?: unknown,
): string {
  const params = new URLSearchParams({ task_id: String(taskId) });
  const normalizedDatasetId = Number(datasetId);
  if (Number.isInteger(normalizedDatasetId) && normalizedDatasetId > 0) {
    params.set('dataset_id', String(normalizedDatasetId));
  }
  const target = inferTrainingTargetFromTaskConfig(taskConfig);
  if (target) params.set('training_target', target);
  return `/training?${params.toString()}`;
}
