import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import ServerSearchInput from './ServerSearchInput';

afterEach(() => cleanup());

describe('ServerSearchInput', () => {
  it('exposes a named search action and submits the trimmed value with Enter', () => {
    const onChange = vi.fn();
    const onSearch = vi.fn();

    render(
      <ServerSearchInput
        value="  alpha  "
        ariaLabel="搜索难例"
        onChange={onChange}
        onSearch={onSearch}
      />,
    );

    const input = screen.getByRole('textbox', { name: '搜索难例' });
    expect(screen.getByRole('button', { name: '搜索难例' })).toBeTruthy();

    fireEvent.keyDown(input, { key: 'Enter', code: 'Enter', charCode: 13 });
    expect(onSearch).toHaveBeenCalledWith('alpha');
  });

  it('forwards the clear action as an empty change', () => {
    const onChange = vi.fn();

    render(
      <ServerSearchInput
        value="alpha"
        ariaLabel="搜索难例"
        onChange={onChange}
        onSearch={vi.fn()}
      />,
    );

    const clear = document.querySelector('.ant-input-clear-icon');
    expect(clear).toBeTruthy();
    fireEvent.mouseDown(clear!);
    fireEvent.click(clear!);
    expect(onChange).toHaveBeenCalledWith('');
  });
});
