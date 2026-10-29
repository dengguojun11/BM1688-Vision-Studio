import type { AnnotationData } from '@labelu/image';

import type {
  AllAnnotationMapping,
  AllAnnotationType,
  GlobalAnnotation,
  AnnotationWithTool,
} from './context/annotation.context';

type PreAnnotations = Partial<Record<AllAnnotationType, (AnnotationData | GlobalAnnotation)[]>>;
interface MergePreAnnotationOptions {
  overwriteExistingAnnotations?: boolean;
  replaceTools?: AllAnnotationType[];
}

const GLOBAL_TOOLS = ['tag', 'text'] as const;
const GEOMETRY_TOOLS = ['rect', 'polygon', 'point', 'line'] as const;

/** Keep on-canvas geometry when AI refreshes tag-only pre-annotations. */
export function preserveGeometryAnnotations(
  base: AllAnnotationMapping,
  preserved: AllAnnotationMapping,
): AllAnnotationMapping {
  if (!preserved || Object.keys(preserved).length === 0) {
    return base;
  }

  const geometrySet = new Set<AllAnnotationType>(GEOMETRY_TOOLS);
  const merged: AllAnnotationMapping = { ...base };

  Object.entries(preserved).forEach(([id, annotation]) => {
    if (geometrySet.has(annotation.tool as AllAnnotationType) && !merged[id]) {
      merged[id] = annotation;
    }
  });

  return merged;
}

function isGlobalTool(tool: string): tool is (typeof GLOBAL_TOOLS)[number] {
  return GLOBAL_TOOLS.includes(tool as (typeof GLOBAL_TOOLS)[number]);
}

function getGlobalField(annotation: GlobalAnnotation) {
  return Object.keys(annotation.value ?? {})[0];
}

function hasGlobalValue(annotation: GlobalAnnotation) {
  const field = getGlobalField(annotation);
  const value = field ? annotation.value?.[field] : undefined;

  return Array.isArray(value)
    ? value.some((item) => item !== undefined && item !== null && item !== '')
    : value !== undefined && value !== null && value !== '';
}

/** Geometry pre-annotation refreshes must never discard saved or unsaved global fields. */
export function preserveGlobalAnnotations(
  base: AllAnnotationMapping,
  preserved: AllAnnotationMapping,
): AllAnnotationMapping {
  if (!preserved || Object.keys(preserved).length === 0) {
    return base;
  }

  const merged: AllAnnotationMapping = { ...base };
  Object.values(preserved).forEach((annotation) => {
    if (!isGlobalTool(annotation.tool)) {
      return;
    }

    const preservedGlobal = annotation as GlobalAnnotation & { tool: AllAnnotationType };
    const field = getGlobalField(preservedGlobal);
    Object.entries(merged).forEach(([id, existing]) => {
      if (
        existing.tool === annotation.tool &&
        isGlobalTool(existing.tool) &&
        getGlobalField(existing as GlobalAnnotation) === field
      ) {
        delete merged[id];
      }
    });
    merged[annotation.id] = annotation;
  });

  return merged;
}

export function mergePreAnnotationsIntoSample(
  sampleAnnotations: Partial<Record<AllAnnotationType, (AnnotationData | GlobalAnnotation)[]>>,
  preAnnotations: PreAnnotations,
  isPreAnnotationEmpty: boolean,
  options: MergePreAnnotationOptions = {},
): AllAnnotationMapping {
  const mapping: AllAnnotationMapping = {};
  const preAnnotationTools = new Set<AllAnnotationType>(options.replaceTools ?? []);

  if (!isPreAnnotationEmpty) {
    Object.keys(preAnnotations).forEach((key) => {
      preAnnotationTools.add(key as AllAnnotationType);
      preAnnotations[key as AllAnnotationType]?.forEach((item) => {
        mapping[item.id] = {
          ...item,
          tool: key as AllAnnotationType,
        } as AnnotationWithTool;
      });
    });
  }

  Object.keys(sampleAnnotations).forEach((key) => {
    if (options.overwriteExistingAnnotations && preAnnotationTools.has(key as AllAnnotationType)) {
      return;
    }

    sampleAnnotations[key as AllAnnotationType]?.forEach((item) => {
      const sampleItem = {
        ...item,
        tool: key as AllAnnotationType,
      } as AnnotationWithTool;

      if (!isGlobalTool(key)) {
        mapping[item.id] = sampleItem;
        return;
      }

      const sampleGlobal = sampleItem as GlobalAnnotation & { tool: AllAnnotationType };
      const sampleField = getGlobalField(sampleGlobal);
      const matchingPreAnnotation = Object.values(mapping).find((preItem) => {
        return (
          preItem.tool === key &&
          isGlobalTool(preItem.tool) &&
          getGlobalField(preItem as GlobalAnnotation) === sampleField
        );
      });

      if (matchingPreAnnotation && hasGlobalValue(matchingPreAnnotation as GlobalAnnotation) && !hasGlobalValue(sampleGlobal)) {
        delete mapping[matchingPreAnnotation.id];
        mapping[matchingPreAnnotation.id] = matchingPreAnnotation;
        return;
      }

      if (matchingPreAnnotation) {
        delete mapping[matchingPreAnnotation.id];
      }
      mapping[item.id] = sampleItem;
    });
  });

  return mapping;
}
