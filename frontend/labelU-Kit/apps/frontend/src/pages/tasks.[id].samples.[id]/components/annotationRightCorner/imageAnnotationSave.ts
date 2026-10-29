import { scaleImageAnnotationsToOriginal } from '@/utils/imageAnnotationGeometry';

export interface ImageAnnotationSnapshotLike {
  annotationsWithGlobal: Record<string, any>;
  originalWidth?: number | null;
  originalHeight?: number | null;
  displayWidth?: number | null;
  displayHeight?: number | null;
  rotate?: number | null;
}

export const exportImageAnnotations = (
  annotationsWithGlobal: Record<string, any>,
): Partial<Record<string, any[]>> => {
  const result: Partial<Record<string, any[]>> = {};

  Object.values(annotationsWithGlobal).forEach((item: any) => {
    const { visible, tool, ...rest } = item;
    const toolName = tool || item.type;

    if (!toolName) {
      return;
    }

    if (!result[toolName]) {
      result[toolName] = [];
    }

    result[toolName]!.push(rest);
  });

  return result;
};

export function buildImageAnnotationSaveResult(snapshot: ImageAnnotationSnapshotLike) {
  const originalWidth = snapshot.originalWidth ?? 0;
  const originalHeight = snapshot.originalHeight ?? 0;
  const displayWidth = snapshot.displayWidth ?? originalWidth;
  const displayHeight = snapshot.displayHeight ?? originalHeight;
  const result: any = {
    width: originalWidth,
    height: originalHeight,
    rotate: snapshot.rotate ?? 0,
  };

  // `sample.data.result` must always stay in original-image coordinates.
  // When the page is rendering `annotation_display_url`, save-back must use the
  // actual loaded display size instead of assuming the 1672px display cap.
  const originalAnnotations = scaleImageAnnotationsToOriginal(
    exportImageAnnotations(snapshot.annotationsWithGlobal),
    originalWidth,
    originalHeight,
    displayWidth,
    displayHeight,
  );

  Object.keys(originalAnnotations).forEach((item) => {
    if (originalAnnotations?.[item]?.length) {
      result[item + 'Tool'] = {
        toolName: item + 'Tool',
        result: originalAnnotations[item],
      };
    }
  });

  return result;
}
