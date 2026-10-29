import { SampleState, type SampleResponse } from '@/api/types';

export const shouldLoadCanvasPreAnnotations = (
  sampleState?: SampleState,
  options?: {
    manual?: boolean;
  },
) => options?.manual === true || sampleState === SampleState.NEW;

const GEOMETRY_TOOL_KEYS = ['rectTool', 'polygonTool', 'pointTool', 'lineTool'] as const;

function parseSampleResult(raw: unknown): Record<string, any> {
  if (!raw) {
    return {};
  }
  if (typeof raw === 'string') {
    try {
      const parsed = JSON.parse(raw);
      return parsed && typeof parsed === 'object' ? parsed : {};
    } catch {
      return {};
    }
  }
  return raw && typeof raw === 'object' ? (raw as Record<string, any>) : {};
}

export function sampleHasGeometryAnnotations(sample?: Pick<SampleResponse, 'data'> | null): boolean {
  const result = parseSampleResult(sample?.data?.result);
  return GEOMETRY_TOOL_KEYS.some((toolKey) => {
    const toolPayload = result?.[toolKey];
    const items = toolPayload && typeof toolPayload === 'object' ? toolPayload.result : undefined;
    return Array.isArray(items) && items.length > 0;
  });
}

export function shouldAutoLoadCanvasPreAnnotationsForSample(
  sample?:
    | Pick<
        SampleResponse,
        | 'state'
        | 'data'
        | 'is_pre_annotated'
        | 'is_yolo_pre_annotated'
        | 'is_experiment_dataset_pre_annotated'
        | 'is_vlx_pre_annotated'
      >
    | null,
  options?: {
    manual?: boolean;
  },
): boolean {
  if (options?.manual === true) {
    return true;
  }
  if (sample?.state === SampleState.NEW) {
    return true;
  }
  // VLX is an explicit image-detection preview request. Load it even when
  // the sample already has saved geometry so the user can compare the model
  // boxes on the canvas without mutating the saved annotation.
  if (sample?.is_vlx_pre_annotated) {
    return true;
  }
  // YOLO batch/current-image preannotation is also an explicit detection
  // preview. A completed sample may already have human geometry, but the
  // generated YOLO row still needs to be loaded for the canvas.
  if (sample?.is_yolo_pre_annotated) {
    return true;
  }
  const hasPreAnnotationSource = Boolean(
    sample?.is_pre_annotated ||
      sample?.is_yolo_pre_annotated ||
      sample?.is_experiment_dataset_pre_annotated ||
      sample?.is_vlx_pre_annotated,
  );
  return hasPreAnnotationSource && !sampleHasGeometryAnnotations(sample);
}
