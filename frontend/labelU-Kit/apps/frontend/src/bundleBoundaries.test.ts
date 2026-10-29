import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

const source = (path: string) => readFileSync(resolve(process.cwd(), path), 'utf8');

describe('frontend bundle boundaries', () => {
  it('loads media annotator styles only with the annotation route', () => {
    const entry = source('src/index.tsx');
    const annotationRoute = source('src/pages/tasks.[id].samples.[id]/index.tsx');

    expect(entry).not.toContain('@labelu/video-annotator-react/dist/style.css');
    expect(entry).not.toContain('@labelu/audio-annotator-react/dist/style.css');
    expect(annotationRoute).toContain("import '@labelu/video-annotator-react/dist/style.css';");
    expect(annotationRoute).toContain("import '@labelu/audio-annotator-react/dist/style.css';");
  });

  it('keeps the preload runtime separate and avoids a catch-all vendor chunk', () => {
    const viteConfig = source('vite.config.ts');

    expect(viteConfig).toContain("normalizedId.includes('vite/preload-helper')");
    expect(viteConfig).toContain("return 'runtime'");
    expect(viteConfig).not.toContain("return 'vendor-misc'");
  });

  it('keeps operator probes on the backend when using the UI dev entrypoint', () => {
    const viteConfig = source('vite.config.ts');

    for (const route of ['/docs', '/api-docs', '/healthz', '/readyz', '/version']) {
      expect(viteConfig).toContain(`'${route}'`);
    }
  });

  it('does not expose video assets through the static application entry', () => {
    const html = source('dist/index.html');
    const entryName = html.match(/src="\/assets\/(index-[^"]+\.js)"/)?.[1];

    if (!entryName) {
      throw new Error('Production entry chunk is missing; run the frontend build before this test.');
    }
    const entry = source(`dist/assets/${entryName}`);
    const workspaceDependencies = entry.match(/import\("\.\/workspace-[^"]+"\),\[([^\]]*)\]/)?.[1];

    expect(html).not.toContain('vendor-video');
    expect(html).not.toContain('vendor-misc');
    expect(html).toContain('runtime-');
    expect(html).toContain('vendor-react-');
    if (!workspaceDependencies) {
      throw new Error('Workspace lazy dependency list is missing from the production entry.');
    }
    expect(workspaceDependencies).not.toContain('vendor-video');
    expect(workspaceDependencies).not.toContain('vendor-misc');
  });

  it('does not import the annotation component barrel from the login page', () => {
    const loginStyle = source('src/pages/login/style.ts');

    expect(loginStyle).not.toContain('@labelu/components-react');
  });
});
