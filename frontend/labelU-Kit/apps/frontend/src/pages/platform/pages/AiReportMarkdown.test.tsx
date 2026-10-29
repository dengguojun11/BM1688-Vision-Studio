import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import AiReportMarkdown from './AiReportMarkdown';

describe('AiReportMarkdown', () => {
  it('renders report headings, emphasis, lists and tables as readable document structure', () => {
    const source = [
      '# YOLO 训练评估报告',
      '',
      '## 1. 结论摘要',
      '',
      '1. **数据风险高于模型风险**：建议先检查 `person` 样本。',
      '2. 质量结论以 best.pt 复验为准。',
      '',
      '| 指标 | val | test |',
      '|---|---:|---:|',
      '| mAP50 | 0.457 | 0.850 |',
      '| recall | 0.412 | 0.900 |',
    ].join('\n');

    const { container } = render(<AiReportMarkdown source={source} />);

    expect(screen.getByRole('heading', { level: 1, name: 'YOLO 训练评估报告' })).toBeTruthy();
    expect(screen.getByRole('heading', { level: 2, name: '1. 结论摘要' })).toBeTruthy();
    expect(screen.getByText('数据风险高于模型风险').tagName).toBe('STRONG');
    expect(screen.getByText('person').tagName).toBe('CODE');
    expect(screen.getAllByRole('listitem')).toHaveLength(2);
    expect(screen.getByRole('table')).toBeTruthy();
    expect(screen.getByRole('cell', { name: '0.457' })).toBeTruthy();
    expect(container.querySelector('.report-table-scroll')).toBeTruthy();
    expect(screen.queryByText('|---|---:|---:|')).toBeNull();
  });

  it('preserves code blocks without rendering their contents as report markup', () => {
    const source = ['## 训练执行要点', '', '```python', 'model.train(', '    epochs=50,', '    optimizer="AdamW",', ')', '```'].join(
      '\n',
    );

    const { container } = render(<AiReportMarkdown source={source} />);

    const codeBlock = container.querySelector('pre[data-language="python"] code');
    expect(codeBlock?.textContent).toContain('model.train(\n    epochs=50,');
    expect(codeBlock?.textContent).toContain('optimizer="AdamW"');
    expect(screen.queryByRole('heading', { name: 'model.train(' })).toBeNull();
  });
});
