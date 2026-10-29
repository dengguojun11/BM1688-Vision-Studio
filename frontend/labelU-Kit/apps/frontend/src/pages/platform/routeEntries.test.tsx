import { lazy, Suspense } from 'react';
import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { PlatformRouteFallback } from './PlatformRoute';
import { platformPageLoaders } from './routeEntries';

describe('platform route entry points', () => {
  it('keeps every platform page behind its own explicit loader', async () => {
    expect(Object.keys(platformPageLoaders)).toEqual([
      'workspace',
      'datasets',
      'ingestion',
      'annotationReview',
      'algorithms',
      'rules',
      'providers',
      'training',
      'models',
      'evolution',
      'edgeTaskCenter',
      'admin',
    ]);

    const loaders = Object.values(platformPageLoaders);
    expect(new Set(loaders).size).toBe(loaders.length);
    expect(loaders.every((loader) => typeof loader === 'function')).toBe(true);
  });

  it('shows a stable accessible loading state until a lazy route resolves', async () => {
    const loader = vi.fn(() => new Promise<{ default: () => JSX.Element }>(() => undefined));
    const LazyPage = lazy(loader);

    render(
      <Suspense fallback={<PlatformRouteFallback />}>
        <LazyPage />
      </Suspense>,
    );

    const loading = screen.getByRole('status', { name: '页面加载中' });
    expect(loading.style.minHeight).toBe('240px');
    expect(loading.style.width).toBe('100%');
    expect(loader).toHaveBeenCalledTimes(1);
  });
});
