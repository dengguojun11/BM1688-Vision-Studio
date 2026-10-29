import { act } from 'react';
import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import EvolutionDataFlow from './EvolutionDataFlow';
import { evolutionPreviewSamples } from './previewSamples.generated';

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe('EvolutionDataFlow', () => {
  it('rotates through real dataset samples with source and progress', () => {
    vi.useFakeTimers();
    const { container } = render(<EvolutionDataFlow active preview />);

    const image = screen.getByRole('img', { name: `数据集图片：${evolutionPreviewSamples[0].fileName}` });
    expect(image.getAttribute('src')).toBe(evolutionPreviewSamples[0].url);
    expect(screen.getByText('训练任务 2 · 测试集')).toBeTruthy();
    expect(screen.getByText('样本接入进化链路')).toBeTruthy();
    expect(screen.getByText(/冻结快照/)).toBeTruthy();
    expect(container.querySelectorAll('i').length).toBeGreaterThanOrEqual(10);

    act(() => vi.advanceTimersByTime(11000));
    const nextImage = screen.getByRole('img', { name: `数据集图片：${evolutionPreviewSamples[1].fileName}` });
    expect(nextImage.getAttribute('src')).toBe(evolutionPreviewSamples[1].url);
    expect(nextImage.getAttribute('src')).not.toBe(image.getAttribute('src'));
  });

  it('never invents a sample image outside preview mode', () => {
    render(<EvolutionDataFlow active={false} preview={false} />);

    expect(screen.queryByRole('img', { name: /数据集图片/ })).toBeNull();
    expect(screen.queryByText('样本接入进化链路')).toBeNull();
    expect(screen.getByText(/难例样本/)).toBeTruthy();
  });
});
