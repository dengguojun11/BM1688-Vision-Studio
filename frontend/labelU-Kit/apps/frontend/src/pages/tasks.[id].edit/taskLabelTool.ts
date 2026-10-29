import { ImageToolName } from '@/enums';

export const TASK_LABEL_TOOL_NAMES = [
  ImageToolName.Rect,
  ImageToolName.Polygon,
  ImageToolName.Point,
  ImageToolName.Line,
  ImageToolName.Cuboid,
] as const;

export function findTaskLabelTool(tools: any[] | undefined, preferredToolName?: string) {
  if (!Array.isArray(tools)) {
    return undefined;
  }

  if (preferredToolName && TASK_LABEL_TOOL_NAMES.includes(preferredToolName as any)) {
    const preferredTool = tools.find((item) => item?.tool === preferredToolName);
    if (preferredTool) {
      return preferredTool;
    }
  }

  for (const toolName of TASK_LABEL_TOOL_NAMES) {
    const tool = tools.find((item) => item?.tool === toolName);
    if (tool) {
      return tool;
    }
  }

  return undefined;
}

export function getTaskLabelIds(tools: any[] | undefined, preferredToolName?: string) {
  const attributes = findTaskLabelTool(tools, preferredToolName)?.config?.attributes;
  if (!Array.isArray(attributes)) {
    return [];
  }

  return Array.from(
    new Set(
      attributes
        .map((item: any) => Number(item?.labelId))
        .filter((value: number) => Number.isInteger(value) && value > 0),
    ),
  );
}
