import { SampleState } from '@/api/types';

type PreAnnotations = Partial<Record<string, unknown[]>>;

const GEOMETRY_TOOL_KEYS = ['rect', 'polygon', 'point', 'line'] as const;

export function preAnnotationsHasGeometry(preAnnotations: PreAnnotations): boolean {
  return GEOMETRY_TOOL_KEYS.some((key) => (preAnnotations[key]?.length ?? 0) > 0);
}

function pickGlobalPreAnnotations(preAnnotations: PreAnnotations): PreAnnotations | undefined {
  const globalPreAnnotations = {
    ...(preAnnotations.tag?.length ? { tag: preAnnotations.tag } : {}),
    ...(preAnnotations.text?.length ? { text: preAnnotations.text } : {}),
  };

  return Object.keys(globalPreAnnotations).length > 0 ? globalPreAnnotations : undefined;
}

export function getVisiblePreAnnotations(
  sampleState: SampleState | undefined,
  preAnnotations: PreAnnotations,
  options: {
    globalOnlyForAnnotated?: boolean;
    overwriteExistingAnnotations?: boolean;
    currentAnnotationsHaveGeometry?: boolean;
    /** Show an explicit geometry-detection preview alongside saved geometry. */
    allowGeometryForAnnotated?: boolean;
  } = {},
): PreAnnotations | undefined {
  if (options.overwriteExistingAnnotations) {
    return preAnnotations;
  }

  // 未标注样本：完整展示上传/YOLO 等预标注文件中的拉框（globalOnly 仅作用于已标注样本）
  if (sampleState === SampleState.NEW) {
    return preAnnotations;
  }

  if (!options.globalOnlyForAnnotated) {
    return undefined;
  }

  // An explicit AI detection run is a preview action. Keep its boxes visible
  // even when the sample already has formal geometry; ImageAnnotator merges
  // them without replacing saved annotations when overwrite is false.
  if (options.allowGeometryForAnnotated && preAnnotationsHasGeometry(preAnnotations)) {
    return preAnnotations;
  }

  if (options.currentAnnotationsHaveGeometry === false && preAnnotationsHasGeometry(preAnnotations)) {
    return preAnnotations;
  }

  return pickGlobalPreAnnotations(preAnnotations);
}
