import { lazy, Suspense } from 'react';
import type React from 'react';
import { Spin } from 'antd';
import { Outlet } from 'react-router-dom';

const PlatformLayout = lazy(() => import('./layout'));

export function PlatformRouteFallback() {
  return (
    <div
      role="status"
      aria-live="polite"
      aria-label="页面加载中"
      style={{ display: 'grid', minHeight: 240, width: '100%', placeItems: 'center' }}
    >
      <Spin spinning />
    </div>
  );
}

export default function PlatformRoute({ children }: { children?: React.ReactNode }) {
  return (
    <Suspense fallback={<PlatformRouteFallback />}>
      <PlatformLayout>{children ?? <Outlet />}</PlatformLayout>
    </Suspense>
  );
}
