/**
 * 此组件可用于react-router>=6.4.0的特性，比如loader等Data API
 * 见：https://reactrouter.com/en/main/routers/picking-a-router
 */
import { Spin } from 'antd';
import React, { Suspense, useEffect, useMemo } from 'react';
import _ from 'lodash';
import type { RouteObject } from 'react-router-dom';
import { createBrowserRouter, createRoutesFromElements, Route, RouterProvider, useMatches } from 'react-router-dom';

import type { Match } from '@/components/Breadcrumb';
import { formatDocumentTitle } from '@/utils/product';

export type RouteWithParent = RouteObject & {
  parent: RouteWithParent | null;
};

// 使用 document.title 替代 react-document-title，避免 componentWillMount 弃用警告
function RouteWithTitle({ children }: { children: React.ReactNode }) {
  const matches = useMatches() as Match[];
  const title = _.chain(matches)
    .filter((match) => Boolean(match.handle?.crumb))
    .map((match) => match.handle.crumb!(match.data))
    .last()
    .value() as string;

  useEffect(() => {
    document.title = formatDocumentTitle(title);
  }, [title]);

  return <>{children}</>;
}

function RouteLoadingFallback() {
  return (
    <div
      role="status"
      aria-live="polite"
      aria-label="页面加载中"
      style={{
        display: 'grid',
        minHeight: '100vh',
        width: '100vw',
        placeItems: 'center',
        background: '#f5f7fb',
      }}
    >
      <div style={{ textAlign: 'center' }}>
        <Spin spinning />
        <div style={{ marginTop: 10, color: '#64748b', fontSize: 13 }}>正在加载模块</div>
      </div>
    </div>
  );
}

function mapRoutes(
  inputRoutes: RouteObject[],
  parentPath: string = '',
  parentRoute: RouteWithParent | null = null,
): React.ReactNode {
  return inputRoutes.map((route) => {
    const { path, element, children, index, ...restProps } = route;
    const routeWithParent: RouteWithParent = { ...route, parent: parentRoute };
    const comp = (
      <RouteWithTitle key={`${parentPath}-${path}`}>
        <Suspense fallback={<RouteLoadingFallback />}>
          {element}
        </Suspense>
      </RouteWithTitle>
    );

    if (index) {
      return (
        <Route
          index={Boolean(index)}
          key={`${parentPath}-${path}`}
          path={undefined}
          element={comp as React.ReactElement}
          {...(restProps as any)}
        />
      );
    }

    return (
      <Route key={`${parentPath}-${path}`} path={path} element={comp as React.ReactElement} {...(restProps as any)}>
        {Array.isArray(children) ? mapRoutes(children, path, routeWithParent) : null}
      </Route>
    );
  });
}

export interface RouterProps {
  routes: RouteObject[];
  basename?: string;
}

export default function RouterContainer({ routes, basename }: RouterProps) {
  const router = useMemo(
    () =>
      createBrowserRouter(createRoutesFromElements(mapRoutes(routes)), {
        basename,
      }),
    [basename, routes],
  );
  // Keep the shell visibly responsive while a lazy route chunk is loading.
  // The previous icon-only fallback could render as a blank viewport before
  // Ant Design styles were injected, which made slow first navigation look
  // like a failed route.
  const fallback = <RouteLoadingFallback />;

  return <RouterProvider router={router} fallbackElement={fallback} />;
}
