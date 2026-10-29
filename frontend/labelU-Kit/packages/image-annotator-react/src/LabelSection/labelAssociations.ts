import type { ILabel } from '@labelu/interface';
import type { ToolName } from '@labelu/image';

export interface ToolLabelEntry {
  toolName: ToolName;
  label: ILabel;
  count: number;
}

export interface ToolLabelAnnotation {
  tool: ToolName;
  label?: string;
}

type ToolLabelConfig = Partial<Record<ToolName, { labels?: ILabel[] }>>;

export function buildToolLabelEntries(
  tools: ToolName[],
  config: ToolLabelConfig | undefined,
  annotations: ToolLabelAnnotation[],
): ToolLabelEntry[] {
  const counts = new Map<string, number>();

  annotations.forEach((annotation) => {
    if (!annotation.label) {
      return;
    }

    const key = `${annotation.tool}:${annotation.label}`;
    counts.set(key, (counts.get(key) ?? 0) + 1);
  });

  return tools.flatMap((toolName) =>
    (config?.[toolName]?.labels ?? []).map((label) => ({
      toolName,
      label,
      count: counts.get(`${toolName}:${label.value}`) ?? 0,
    })),
  );
}
