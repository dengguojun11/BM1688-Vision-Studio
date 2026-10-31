import { resolve } from 'path';

import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';
// import { viteMockServe } from 'vite-plugin-mock';
import svgr from 'vite-plugin-svgr';
import tsMonoAlias from 'vite-plugin-ts-mono-alias';
import { ViteEjsPlugin } from 'vite-plugin-ejs';

const isOnline = !!process.env.VITE_IS_ONLINE;
const useLocalPackageDist = process.env.NODE_ENV === 'production' || !!process.env.DIST;
const backendPort = process.env.VITE_BACKEND_PORT || '8000';
const viteDevPort = Number(process.env.LABELU_VITE_PORT || '3004');
const hmrClientPort = process.env.LABELU_VITE_HMR_CLIENT_PORT
  ? Number(process.env.LABELU_VITE_HMR_CLIENT_PORT)
  : undefined;
console.log('isOnline', isOnline);

// https://vitejs.dev/config/
export default defineConfig({
  base: '/',
  // The platform is history-routed; direct links such as /training must
  // receive index.html from Vite before React Router takes over.
  appType: 'spa',
  publicDir: resolve(__dirname, 'public'),
  envDir: resolve(__dirname, 'env'),
  server: {
    host: '0.0.0.0',
    strictPort: true,
    // 浏览器只连后端端口；HMR WS 由 FastAPI 反代到本机 Vite
    hmr: hmrClientPort
      ? {
          host: '127.0.0.1',
          port: viteDevPort,
          clientPort: hmrClientPort,
          protocol: 'ws',
        }
      : true,
    proxy: {
      '/api': {
        target: `http://127.0.0.1:${backendPort}`,
        changeOrigin: true,
      },
      '/api-docs': {
        target: `http://127.0.0.1:${backendPort}`,
        changeOrigin: true,
      },
      // Keep the long-standing FastAPI documentation bookmarks working when
      // the frontend dev server is used as the local entrypoint.
      '/docs': {
        target: `http://127.0.0.1:${backendPort}`,
        changeOrigin: true,
      },
      '/redoc': {
        target: `http://127.0.0.1:${backendPort}`,
        changeOrigin: true,
      },
      '/openapi.json': {
        target: `http://127.0.0.1:${backendPort}`,
        changeOrigin: true,
      },
      // Keep operator probes available from the same local entrypoint as the
      // platform UI.  Without these explicit rules SPA fallback returns 404.
      '/healthz': {
        target: `http://127.0.0.1:${backendPort}`,
        changeOrigin: true,
      },
      '/readyz': {
        target: `http://127.0.0.1:${backendPort}`,
        changeOrigin: true,
      },
      '/version': {
        target: `http://127.0.0.1:${backendPort}`,
        changeOrigin: true,
      },
      '/internal': {
        target: `http://127.0.0.1:${backendPort}`,
        changeOrigin: true,
      },
      '/ws': {
        target: `ws://127.0.0.1:${backendPort}`,
        ws: true,
        changeOrigin: true,
      },
    },
  },

  optimizeDeps: {
    include: [
      'react',
      'react-dom',
      'react/jsx-runtime',
      'react-dom/client',
      'react-router-dom',
      'antd',
      'lodash-es',
      '@tanstack/react-query',
      'axios',
      'styled-components',
    ],
  },

  plugins: [
    react({ fastRefresh: process.env.NODE_ENV !== 'test' }),
    svgr(),
    ViteEjsPlugin(),
    !process.env.DIST && process.env.NODE_ENV !== 'production' && tsMonoAlias(),
  ].filter(Boolean),
  resolve: {
    dedupe: ['styled-components'],
    alias: {
      '@': resolve(__dirname, 'src/'),
      ...(useLocalPackageDist
        ? {
            '@labelu/i18n': resolve(__dirname, '../../packages/i18n/dist/index.mjs'),
            '@labelu/components-react': resolve(__dirname, '../../packages/components-react/dist/index.mjs'),
            '@labelu/image': resolve(__dirname, '../../packages/image/dist/index.mjs'),
            '@labelu/image-annotator-react': resolve(__dirname, '../../packages/image-annotator-react/dist/index.mjs'),
          }
        : {}),
    },
  },
  build: {
    target: 'es2015',
    minify: 'terser',
    rollupOptions: {
      output: {
        manualChunks(id) {
          const normalizedId = id.replace(/\\/g, '/');
          if (normalizedId.includes('vite/preload-helper') || normalizedId.includes('commonjsHelpers')) return 'runtime';
          if (!normalizedId.includes('node_modules')) return undefined;
          if (normalizedId.includes('/antd/') || normalizedId.includes('ant-design/icons')) return 'vendor-antd';
          if (normalizedId.includes('@tanstack/react-query')) return 'vendor-query';
          if (
            normalizedId.includes('/react@') ||
            normalizedId.includes('/react-dom@') ||
            normalizedId.includes('/react-router') ||
            normalizedId.includes('/scheduler@') ||
            normalizedId.includes('/use-sync-external-store@')
          ) return 'vendor-react';
          if (normalizedId.includes('monaco-editor')) return 'vendor-editor';
          if (
            normalizedId.includes('video.js') ||
            normalizedId.includes('/video-react/') ||
            normalizedId.includes('/video-annotator-react/')
          ) return 'vendor-video';
          return undefined;
        },
      },
    },
    terserOptions: {
      compress: {
        drop_console: false,
        drop_debugger: true,
      },
    },
  },
  test: {
    environment: 'jsdom',
  },
});
