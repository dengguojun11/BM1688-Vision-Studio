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
    /** Session-scoped geometry preview after an explicit detection run. */
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

  // An explicit AI/YOLO current-image run is a transient preview action.
  // Keep its boxes visible for this page session even when the sample already
  // has formal geometry; a refresh or sample navigation ends the preview.
  if (options.allowGeometryForAnnotated && preAnnotationsHasGeometry(preAnnotations)) {
    return preAnnotations;
  }

  return pickGlobalPreAnnotations(preAnnotations);
}
