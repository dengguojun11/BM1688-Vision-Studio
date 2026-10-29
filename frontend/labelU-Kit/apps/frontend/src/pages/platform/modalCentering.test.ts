import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

const source = (path: string) => readFileSync(resolve(process.cwd(), path), 'utf8');

const OPERATOR_MODAL_FILES = [
  'src/pages/platform/DeviceSignatureEvidence.tsx',
  'src/pages/platform/ResourcePage.tsx',
  'src/pages/platform/RuleCenter.tsx',
  'src/pages/platform/pages/admin.tsx',
  'src/pages/platform/pages/algorithms.tsx',
  'src/pages/platform/pages/annotationReview.tsx',
  'src/pages/platform/pages/evolution.tsx',
  'src/pages/platform/pages/ingestion.tsx',
  'src/pages/platform/pages/models.tsx',
  'src/pages/platform/pages/providers.tsx',
  'src/pages/platform/pages/training.tsx',
  'src/pages/tasks/index.tsx',
];

describe('平台弹窗交互契约', () => {
  it('一期操作页面的所有 Modal 都使用居中弹窗', () => {
    for (const file of OPERATOR_MODAL_FILES) {
      const text = source(file);
      // Count both JSX modals and imperative confirmation dialogs.  The
      // latter are rendered by Ant Design as modals too and must keep the
      // same centered interaction contract.
      const modalCount = (text.match(/<Modal\b|Modal\.confirm\(/g) || []).length;
      const centeredCount = (text.match(/\bcentered\b/g) || []).length;
      expect(modalCount, `${file} 应至少包含一个 Modal`).toBeGreaterThan(0);
      expect(centeredCount, `${file} 存在未居中的 Modal`).toBe(modalCount);
    }
  });
});
