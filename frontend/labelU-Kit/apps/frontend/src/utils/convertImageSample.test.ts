import { describe, expect, it } from 'vitest';

import {
  convertImageSample,
  resolveImageAnnotationSourceSize,
  scaleImageAnnotationsBetweenSizes,
  scaleImageAnnotationsForDisplay,
  scaleImageAnnotationsToOriginal,
} from './convertImageSample';

const expectRectCloseTo = (
  actual: any,
  expected: { x: number; y: number; width: number; height: number; label?: string },
) => {
  expect(actual?.x).toBeCloseTo(expected.x);
  expect(actual?.y).toBeCloseTo(expected.y);
  expect(actual?.width).toBeCloseTo(expected.width);
  expect(actual?.height).toBeCloseTo(expected.height);
  if (expected.label !== undefined) {
    expect(actual?.label).toBe(expected.label);
  }
};

describe('image annotation display scaling', () => {
  it('uses the rounded display height when scaling 16:9 images to 1672 max side', () => {
    const original = {
      rect: [
        {
          id: 'box-1',
          x: 960,
          y: 540,
          width: 480,
          height: 270,
        },
      ],
    };

    const display = scaleImageAnnotationsForDisplay(original, 1920, 1080);

    expect(display.rect[0]).toMatchObject({
      x: 836,
      y: 470.5,
      width: 418,
      height: 235.25,
    });
  });

  it('round-trips display annotations back to original coordinates', () => {
    const original = {
      rect: [
        {
          id: 'box-1',
          x: 960,
          y: 540,
          width: 480,
          height: 270,
        },
      ],
    };

    const display = scaleImageAnnotationsForDisplay(original, 1920, 1080);
    const restored = scaleImageAnnotationsToOriginal(display, 1920, 1080);

    expect(restored.rect[0]).toMatchObject(original.rect[0]);
  });

  it('restores annotations with the actual loaded display size instead of assuming 1672px', () => {
    const legacyDisplay = {
      rect: [
        {
          id: 'box-1',
          x: 1456.0333333333333,
          y: 819.01875,
          width: 145.60333333333332,
          height: 81.901875,
        },
      ],
    };

    const restored = scaleImageAnnotationsToOriginal(legacyDisplay, 1920, 1080, 1456.0333333333333, 819.01875);

    expectRectCloseTo(restored.rect[0], {
      x: 1920,
      y: 1080,
      width: 192,
      height: 108,
    });
  });

  it('scales annotations directly between arbitrary source and target sizes', () => {
    const source = {
      rect: [
        {
          id: 'box-1',
          x: 100,
          y: 50,
          width: 40,
          height: 20,
        },
      ],
    };

    const scaled = scaleImageAnnotationsBetweenSizes(source, 1000, 500, 2000, 1000);

    expect(scaled.rect[0]).toMatchObject({
      x: 200,
      y: 100,
      width: 80,
      height: 40,
    });
  });

  it('prefers pre-annotation meta dimensions over sample file dimensions', () => {
    const sourceSize = resolveImageAnnotationSourceSize(1672, 941, 1920, 1080);
    const display = scaleImageAnnotationsForDisplay(
      {
        rect: [
          {
            id: 'box-1',
            x: 718.4584,
            y: 455.7263,
            width: 443.2472,
            height: 449.5157,
          },
        ],
      },
      sourceSize.width,
      sourceSize.height,
    );

    expectRectCloseTo(display.rect[0], {
      x: 718.4584,
      y: 455.7263,
      width: 443.2472,
      height: 449.5157,
    });
  });

  it('falls back to sample file dimensions when pre-annotation meta dimensions are missing', () => {
    const sourceSize = resolveImageAnnotationSourceSize(undefined, undefined, 1920, 1080);
    const display = scaleImageAnnotationsForDisplay(
      {
        rect: [
          {
            id: 'box-1',
            x: 825.024,
            y: 523.044,
            width: 508.992,
            height: 515.916,
          },
        ],
      },
      sourceSize.width,
      sourceSize.height,
    );

    expectRectCloseTo(display.rect[0], {
      x: 718.4584,
      y: 455.7263,
      width: 443.2472,
      height: 449.5157,
    });
  });

  it('keeps experiment dataset results that were saved in annotation display coordinates', () => {
    const sample = {
      id: 94745,
      inner_id: 2,
      is_pre_annotated: false,
      is_experiment_dataset_pre_annotated: true,
      file: {
        id: 1,
        url: '/original.jpg',
        annotation_display_url: '/display.jpg',
        filename: 'sample.jpg',
      },
      data: {
        result: JSON.stringify({
          width: 1920,
          height: 1080,
          rectTool: {
            toolName: 'rectTool',
            result: [
              {
                id: 'box-1',
                x: 718.4584,
                y: 455.7263,
                width: 443.2472,
                height: 449.5157,
                attribute: 'electrombileWithoutHelmet',
              },
            ],
          },
        }),
      },
    } as any;

    const converted = convertImageSample(sample, undefined, { description: 'source=experiment_dataset_formal' });

    expect(converted?.meta?.originalWidth).toBe(1672);
    expect(converted?.meta?.originalHeight).toBe(941);
    expectRectCloseTo(converted?.data.rect?.[0], {
      x: 718.4584,
      y: 455.7263,
      width: 443.2472,
      height: 449.5157,
      label: 'electrombileWithoutHelmet',
    });
  });

  it('keeps experiment dataset results when the backend already returned an object', () => {
    const sample = {
      id: 94745,
      inner_id: 2,
      is_pre_annotated: false,
      is_experiment_dataset_pre_annotated: true,
      file: {
        id: 1,
        url: '/original.jpg',
        annotation_display_url: '/display.jpg',
        filename: 'sample.jpg',
      },
      data: {
        result: {
          width: 1920,
          height: 1080,
          rectTool: {
            toolName: 'rectTool',
            result: [
              {
                id: 'box-1',
                x: 718.4584,
                y: 455.7263,
                width: 443.2472,
                height: 449.5157,
                attribute: 'electrombileWithoutHelmet',
              },
            ],
          },
        },
      },
    } as any;

    const converted = convertImageSample(sample, undefined, { description: 'source=experiment_dataset_formal' });

    expect(converted?.meta?.originalWidth).toBe(1672);
    expect(converted?.meta?.originalHeight).toBe(941);
    expectRectCloseTo(converted?.data.rect?.[0], {
      x: 718.4584,
      y: 455.7263,
      width: 443.2472,
      height: 449.5157,
      label: 'electrombileWithoutHelmet',
    });
  });

  it('keeps legacy external S3 results when the backend marker is not available yet', () => {
    const sample = {
      id: 94745,
      inner_id: 2,
      is_pre_annotated: false,
      file: {
        id: 1,
        url: '/api/v1/datasource-attachments/1',
        annotation_display_url: '/api/v1/datasource-attachments/1/annotation-display',
        filename: 'sample.jpg',
        storage_backend: 's3',
        original_width: null,
        original_height: null,
      },
      data: {
        result: JSON.stringify({
          width: 1920,
          height: 1080,
          rectTool: {
            toolName: 'rectTool',
            result: [
              {
                id: 'box-1',
                x: 718.4584,
                y: 455.7263,
                width: 443.2472,
                height: 449.5157,
              },
            ],
          },
        }),
      },
    } as any;

    const converted = convertImageSample(sample, undefined, { description: 'source=experiment_dataset_formal' });

    expect(converted?.meta?.originalWidth).toBe(1672);
    expect(converted?.meta?.originalHeight).toBe(941);
    expectRectCloseTo(converted?.data.rect?.[0], {
      x: 718.4584,
      y: 455.7263,
      width: 443.2472,
      height: 449.5157,
    });
  });

  it('does not apply the legacy external S3 fallback outside experiment dataset tasks', () => {
    const sample = {
      id: 1,
      inner_id: 1,
      is_pre_annotated: false,
      file: {
        id: 1,
        url: '/api/v1/datasource-attachments/1',
        annotation_display_url: '/api/v1/datasource-attachments/1/annotation-display',
        filename: 'sample.jpg',
        storage_backend: 's3',
      },
      data: {
        result: JSON.stringify({
          width: 1920,
          height: 1080,
          rectTool: {
            toolName: 'rectTool',
            result: [
              {
                id: 'box-1',
                x: 718.4584,
                y: 455.7263,
                width: 443.2472,
                height: 449.5157,
              },
            ],
          },
        }),
      },
    } as any;

    const converted = convertImageSample(sample, undefined, { description: 'source=manual_import' });

    expect(converted?.meta?.originalWidth).toBe(1920);
    expect(converted?.meta?.originalHeight).toBe(1080);
    expectRectCloseTo(converted?.data.rect?.[0], {
      x: 625.6575233333333,
      y: 397.0726373148148,
      width: 385.9944366666667,
      height: 391.661364537037,
    });
  });

  it('does not apply the legacy external S3 fallback when geometry exceeds the display image', () => {
    const sample = {
      id: 1,
      inner_id: 1,
      is_pre_annotated: false,
      file: {
        id: 1,
        url: '/api/v1/datasource-attachments/1',
        annotation_display_url: '/api/v1/datasource-attachments/1/annotation-display',
        filename: 'sample.jpg',
        storage_backend: 's3',
      },
      data: {
        result: JSON.stringify({
          width: 1920,
          height: 1080,
          rectTool: {
            toolName: 'rectTool',
            result: [
              {
                id: 'box-1',
                x: 1700,
                y: 960,
                width: 100,
                height: 80,
              },
            ],
          },
        }),
      },
    } as any;

    const converted = convertImageSample(sample, undefined, { description: 'source=experiment_dataset_formal' });

    expect(converted?.meta?.originalWidth).toBe(1920);
    expect(converted?.meta?.originalHeight).toBe(1080);
    expectRectCloseTo(converted?.data.rect?.[0], {
      x: 1480.4166666666667,
      y: 836.4444444444445,
      width: 87.08333333333333,
      height: 69.70370370370371,
    });
  });

  it('still scales normal original-coordinate results for annotation display images', () => {
    const sample = {
      id: 1,
      inner_id: 1,
      is_pre_annotated: false,
      file: {
        id: 1,
        url: '/original.jpg',
        annotation_display_url: '/display.jpg',
        filename: 'sample.jpg',
      },
      data: {
        result: JSON.stringify({
          width: 1920,
          height: 1080,
          rectTool: {
            toolName: 'rectTool',
            result: [
              {
                id: 'box-1',
                x: 825.024,
                y: 523.044,
                width: 508.992,
                height: 515.916,
              },
            ],
          },
        }),
      },
    } as any;

    const converted = convertImageSample(sample);

    expect(converted?.meta?.originalWidth).toBe(1920);
    expect(converted?.meta?.originalHeight).toBe(1080);
    expectRectCloseTo(converted?.data.rect?.[0], {
      x: 718.4584,
      y: 455.7263,
      width: 443.2472,
      height: 449.5157,
    });
  });
});
