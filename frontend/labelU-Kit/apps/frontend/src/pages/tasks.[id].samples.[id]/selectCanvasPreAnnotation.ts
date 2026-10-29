import _ from 'lodash-es';

/** 单条样本预标注记录（与接口 list 项一致） */
export type PreAnnotationListRow = {
  id?: number;
  data?: string;
  file?: {
    id?: string | number;
    filename?: string;
  } | null;
};

const GENERATED_SOURCE_TYPES = new Set([
  'ai_generated',
  'yolo_generated',
  'tagtool_cls_generated',
  'locateanything_generated',
  'vlx_seek_generated',
  'rex_omni_generated',
]);
const GLOBAL_TOOL_NAMES = ['tagTool', 'textTool'] as const;

function parseRowData(row: PreAnnotationListRow | undefined): Record<string, any> | null {
  if (!row?.data) {
    return null;
  }
  try {
    return JSON.parse(row.data);
  } catch {
    return null;
  }
}

function hasGeometryPreAnnotation(row: PreAnnotationListRow | undefined): boolean {
  const parsed = parseRowData(row);
  const annotations = parsed?.annotations;
  if (!annotations || typeof annotations !== 'object') {
    return false;
  }

  return Object.entries(annotations).some(([toolName, toolPayload]) => {
    if (toolName === 'tagTool' || toolName === 'textTool') {
      return false;
    }
    if (Array.isArray(toolPayload)) {
      return toolPayload.length > 0;
    }
    if (toolPayload && typeof toolPayload === 'object' && 'result' in toolPayload) {
      return (
        Array.isArray((toolPayload as { result?: unknown }).result) &&
        (toolPayload as { result: unknown[] }).result.length > 0
      );
    }
    return false;
  });
}

export function isUploadedPreAnnotationRow(row: PreAnnotationListRow | undefined): boolean {
  if (!row) {
    return false;
  }
  if (row.file?.id != null || row.file?.filename) {
    return true;
  }
  try {
    if (!row.data) {
      return false;
    }
    const parsed = JSON.parse(row.data);
    return (parsed?.meta?.source_type ?? '') === 'file_upload';
  } catch {
    return false;
  }
}

export function isGeneratedPreAnnotationRow(row: PreAnnotationListRow | undefined): boolean {
  const parsed = parseRowData(row);
  const st = parsed?.meta?.source_type;
  return typeof st === 'string' && GENERATED_SOURCE_TYPES.has(st);
}

function hasGlobalPreAnnotation(row: PreAnnotationListRow | undefined): boolean {
  const annotations = parseRowData(row)?.annotations;
  if (!annotations || typeof annotations !== 'object') {
    return false;
  }

  return GLOBAL_TOOL_NAMES.some((toolName) => {
    const payload = annotations[toolName];
    const result = Array.isArray(payload)
      ? payload
      : payload && typeof payload === 'object' && Array.isArray(payload.result)
      ? payload.result
      : [];
    return result.length > 0;
  });
}

function resultItems(payload: any): any[] {
  if (Array.isArray(payload)) {
    return payload;
  }
  return payload && typeof payload === 'object' && Array.isArray(payload.result) ? payload.result : [];
}

function mergeGlobalToolPayload(primary: any, secondary: any): any {
  if (!primary) {
    return secondary;
  }
  if (!secondary) {
    return primary;
  }

  const primaryItems = resultItems(primary);
  const secondaryItems = resultItems(secondary);
  if (!primaryItems.length && !secondaryItems.length) {
    return primary;
  }

  const mergedItems: any[] = [];
  const seen = new Set<string>();
  [...primaryItems, ...secondaryItems].forEach((item) => {
    let key: string;
    try {
      key = JSON.stringify(item);
    } catch {
      key = String(item);
    }
    if (!seen.has(key)) {
      seen.add(key);
      mergedItems.push(item);
    }
  });

  if (Array.isArray(primary)) {
    return mergedItems;
  }
  return { ...(secondary ?? {}), ...(primary ?? {}), result: mergedItems };
}

/**
 * VLX/YOLO and TagTool can create separate generated rows for the same image.
 * Keep the selected geometry row, but carry global results from the newest
 * generated global-only row so the canvas does not make them appear missing.
 */
function mergeGeneratedGlobalAnnotations(
  selected: PreAnnotationListRow,
  rows: PreAnnotationListRow[],
): PreAnnotationListRow {
  if (!isGeneratedPreAnnotationRow(selected) || !hasGeometryPreAnnotation(selected)) {
    return selected;
  }

  const selectedData = parseRowData(selected);
  const selectedAnnotations = selectedData?.annotations;
  if (!selectedData || !selectedAnnotations || typeof selectedAnnotations !== 'object') {
    return selected;
  }

  const globalRows = rows
    .filter((row) => row !== selected && isGeneratedPreAnnotationRow(row) && hasGlobalPreAnnotation(row))
    .reverse();
  if (!globalRows.length) {
    return selected;
  }

  const mergedData = {
    ...selectedData,
    annotations: { ...selectedAnnotations },
  };
  let changed = false;
  for (const toolName of GLOBAL_TOOL_NAMES) {
    let mergedPayload = mergedData.annotations[toolName];
    for (const row of globalRows) {
      const sourcePayload = parseRowData(row)?.annotations?.[toolName];
      if (resultItems(sourcePayload).length) {
        const nextPayload = mergeGlobalToolPayload(mergedPayload, sourcePayload);
        if (nextPayload !== mergedPayload) {
          mergedPayload = nextPayload;
          changed = true;
        }
      }
    }
    if (mergedPayload) {
      mergedData.annotations[toolName] = mergedPayload;
    }
  }

  return changed ? { ...selected, data: JSON.stringify(mergedData) } : selected;
}

function isCanvasOverwriteRow(row: PreAnnotationListRow | undefined): boolean {
  const parsed = parseRowData(row);
  return GENERATED_SOURCE_TYPES.has(parsed?.meta?.source_type) && parsed?.meta?.apply_to_canvas_overwrite === true;
}

/**
 * 画布展示用预标注：有上传文件时始终用上传记录，不被后写入的 AI/YOLO 覆盖。
 */
export function selectCanvasPreAnnotationRow(
  rows: PreAnnotationListRow[] | undefined,
): PreAnnotationListRow | undefined {
  const list = rows ?? [];
  if (!list.length) {
    return undefined;
  }

  const canvasOverrides = list.filter(isCanvasOverwriteRow);
  if (canvasOverrides.length) {
    return mergeGeneratedGlobalAnnotations(canvasOverrides[canvasOverrides.length - 1], list);
  }

  const uploaded = list.filter(isUploadedPreAnnotationRow);
  if (uploaded.length) {
    return uploaded[0];
  }

  const generated = list.filter(isGeneratedPreAnnotationRow);
  const generatedWithGeometry = generated.filter(hasGeometryPreAnnotation);
  if (generatedWithGeometry.length) {
    return mergeGeneratedGlobalAnnotations(generatedWithGeometry[generatedWithGeometry.length - 1], list);
  }

  const anyWithGeometry = list.filter(hasGeometryPreAnnotation);
  if (anyWithGeometry.length) {
    return mergeGeneratedGlobalAnnotations(anyWithGeometry[anyWithGeometry.length - 1], list);
  }

  if (generated.length) {
    return generated[generated.length - 1];
  }

  return _.last(list);
}
