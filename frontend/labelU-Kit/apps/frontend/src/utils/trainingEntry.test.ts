import { describe, expect, it } from 'vitest';

import { inferTrainingTargetFromTaskConfig, trainingEntryPath } from './trainingEntry';

const detectionConfig = {
  defaultRectPresetId: 'plateDetection',
  tools: [
    { tool: 'rectTool', config: { attributes: [{ value: 'doubleEP' }] } },
    { tool: 'polygonTool', config: { attributes: [{ value: 'platePoint' }] } },
  ],
};

const recognitionConfig = {
  defaultRectPresetId: 'plateRecognition',
  tools: [
    {
      tool: 'rectTool',
      config: {
        attributes: [
          { value: 'doubleEP' },
          { value: 'plateNumber' },
          { value: 'color' },
          { value: 'plateType' },
        ],
      },
    },
    { tool: 'polygonTool', config: { attributes: [{ value: 'platePoint' }] } },
  ],
};

describe('training entry target normalization', () => {
  it('routes the detection preset to the detection-only target', () => {
    expect(inferTrainingTargetFromTaskConfig(detectionConfig)).toBe('plate_detection');
    expect(trainingEntryPath(42, 7, detectionConfig)).toBe(
      '/training?task_id=42&dataset_id=7&training_target=plate_detection',
    );
  });

  it('routes historical composite plate tasks to the detection-only target', () => {
    expect(inferTrainingTargetFromTaskConfig(recognitionConfig)).toBe('plate_detection');
    expect(trainingEntryPath(42, 7, recognitionConfig)).toBe(
      '/training?task_id=42&dataset_id=7&training_target=plate_detection',
    );
  });

  it('recognizes imported historical geometry without a preset marker', () => {
    expect(inferTrainingTargetFromTaskConfig({
      tools: recognitionConfig.tools,
    })).toBe('plate_detection');
  });

  it('does not add a specialized target to ordinary tasks', () => {
    expect(inferTrainingTargetFromTaskConfig({
      tools: [{ tool: 'rectTool', config: { attributes: [{ value: 'vehicle' }] } }],
    })).toBeNull();
    expect(trainingEntryPath(42)).toBe('/training?task_id=42');
  });
});
