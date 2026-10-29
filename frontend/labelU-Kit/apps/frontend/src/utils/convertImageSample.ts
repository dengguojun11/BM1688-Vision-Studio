import _ from 'lodash';
import type { ImageSample } from '@labelu/image-annotator-react';
import { omit } from 'lodash/fp';
import type { ToolName } from '@labelu/image';
import { TOOL_NAMES } from '@labelu/image';

import type { ParsedResult, SampleResponse } from '@/api/types';

import { jsonParse } from './index';
import {
  getRectLabelOptionsFromTaskConfig,
  normalizeImageAnnotationRectLabels,
  type TaskLabelOption,
} from './canonicalRectLabel';
import {
  ANNOTATION_DISPLAY_MAX_SIDE,
  getAnnotationDisplaySize,
  imageAnnotationsFitWithinSize,
  resolveImageAnnotationSourceSize,
  scaleImageAnnotationsBetweenSizes,
  scaleImageAnnotationsForDisplay,
  scaleImageAnnotationsToOriginal,
} from './imageAnnotationGeometry';
export {
  ANNOTATION_DISPLAY_MAX_SIDE,
  getAnnotationDisplaySize,
  imageAnnotationsFitWithinSize,
  resolveImageAnnotationSourceSize,
  scaleImageAnnotationsBetweenSizes,
  scaleImageAnnotationsForDisplay,
  scaleImageAnnotationsToOriginal,
};

export function convertImageAnnotations(result: ParsedResult) {
  const resultByTool = result as ParsedResult & Record<string, any>;
  // annotation
  const pool = [
    ['line', 'lineTool'],
    ['point', 'pointTool'],
    ['rect', 'rectTool'],
    ['polygon', 'polygonTool'],
    ['cuboid', 'cuboidTool'],
    ['relation', 'relationTool'],
    ['text', 'textTool'],
    ['tag', 'tagTool'],
  ] as const;

  return _.chain(pool)
    .map(([type, key]) => {
      if (!resultByTool[key] && TOOL_NAMES.includes(type as ToolName)) {
        return;
      }

      const items = _.get(result, [key, 'result']) || _.get(result, [type, 'result'], []);

      return [
        type,
        items.map((item: any) => {
          const resultItem = {
            ...omit(['attribute'])(item),
            label: item.attribute ?? item.label,
          } as any;

          if (type === 'line' || type === 'polygon') {
            return {
              ...omit(['pointList'])(resultItem),
              type: resultItem.type ?? 'line',
              points: item.pointList ?? item.points,
            };
          }

          return resultItem;
        }),
      ];
    })
    .compact()
    .fromPairs()
    .value();
}

export function convertImageSample(
  sample: SampleResponse | undefined,
  taskConfig?: unknown,
  taskContext?: { description?: string | null },
): ImageSample | undefined {
  if (!sample) {
    return;
  }

  const id = sample.id!;
  const usesAnnotationDisplayImage = !!sample.file.annotation_display_url;
  const url = sample.file.annotation_display_url || sample.file.url;

  let resultParsed: any = {};
  if (sample?.data?.result && !_.isNull(sample?.data?.result)) {
    resultParsed = jsonParse(sample.data.result);
  }

  const rectLabels: TaskLabelOption[] = getRectLabelOptionsFromTaskConfig(taskConfig);
  const data = convertImageAnnotations(resultParsed);
  const normalizedData = normalizeImageAnnotationRectLabels(data, rectLabels);
  const annotationWidth = resultParsed.width ?? sample.file.original_width;
  const annotationHeight = resultParsed.height ?? sample.file.original_height;
  const annotationSourceSize = resolveImageAnnotationSourceSize(annotationWidth, annotationHeight);
  const annotationDisplaySize = getAnnotationDisplaySize(
    annotationSourceSize.width,
    annotationSourceSize.height,
  );
  const geometryFitsAnnotationDisplaySize =
    !!annotationDisplaySize.width &&
    !!annotationDisplaySize.height &&
    imageAnnotationsFitWithinSize(normalizedData, annotationDisplaySize.width, annotationDisplaySize.height);
  const isExperimentDatasetTask = String(taskContext?.description ?? '').includes('experiment_dataset');
  const isLegacyExternalDisplayCoordinateResult =
    isExperimentDatasetTask &&
    sample.file.storage_backend === 's3' &&
    !sample.file.original_width &&
    !sample.file.original_height &&
    geometryFitsAnnotationDisplaySize;
  const useDisplayCoordinateResult =
    usesAnnotationDisplayImage &&
    !!annotationSourceSize.width &&
    !!annotationSourceSize.height &&
    !!annotationDisplaySize.width &&
    !!annotationDisplaySize.height &&
    (annotationSourceSize.width !== annotationDisplaySize.width ||
      annotationSourceSize.height !== annotationDisplaySize.height) &&
    (!!sample.is_experiment_dataset_pre_annotated || isLegacyExternalDisplayCoordinateResult);
  const effectiveAnnotationWidth = useDisplayCoordinateResult
    ? annotationDisplaySize.width
    : annotationSourceSize.width;
  const effectiveAnnotationHeight = useDisplayCoordinateResult
    ? annotationDisplaySize.height
    : annotationSourceSize.height;
  const displayData = usesAnnotationDisplayImage
    ? scaleImageAnnotationsForDisplay(normalizedData, effectiveAnnotationWidth, effectiveAnnotationHeight)
    : normalizedData;

  return {
    id,
    url,
    data: displayData,
    meta: {
      ..._.pick(resultParsed, ['width', 'height', 'rotate']),
      originalWidth: effectiveAnnotationWidth,
      originalHeight: effectiveAnnotationHeight,
    } as ImageSample['meta'] & { originalWidth?: number; originalHeight?: number },
  };
}
