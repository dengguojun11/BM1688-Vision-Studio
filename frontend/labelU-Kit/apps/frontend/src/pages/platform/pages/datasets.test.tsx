import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import request from '@/api/request';
import {
  CreateVersionModal,
  DatasetLabelContractModal,
  VersionGovernanceModal,
  datasetVersionStatus,
  fetchSourceTasks,
  fetchDatasets,
  parseDatasetMetadata,
} from './datasets';
import DatasetsPage from './datasets';
import { AnnotationPromptModal } from './annotationPromptModal';

vi.mock('@/api/request', () => ({ default: { get: vi.fn(), post: vi.fn(), patch: vi.fn() } }));
vi.mock('../project', () => ({
  usePlatformProject: () => ({
    projectId: 1,
    projectsQuery: { isLoading: false, data: [{ id: 1, name: '本地项目' }] },
  }),
}));
const resourcePageState = vi.hoisted(() => ({ props: null as any }));
vi.mock('../ResourcePage', () => ({
  default: (props: any) => {
    resourcePageState.props = props;
    return <div data-testid="resource-page-row-actions">{props.rowActions?.({ id: 7, name: '占道经营' })}{props.beforeTable}</div>;
  },
}));

window.matchMedia = ((query: string) => ({
  matches: false,
  media: query,
  onchange: null,
  addListener: () => undefined,
  removeListener: () => undefined,
  addEventListener: () => undefined,
  removeEventListener: () => undefined,
  dispatchEvent: () => false,
})) as typeof window.matchMedia;

const mockedGet = vi.mocked(request.get);
const mockedPost = vi.mocked(request.post);
const mockedPatch = vi.mocked(request.patch);

afterEach(() => {
  cleanup();
  mockedGet.mockReset();
  mockedPost.mockReset();
  mockedPatch.mockReset();
  resourcePageState.props = null;
});

function renderWithQuery(ui: React.ReactNode) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>);
}

describe('dataset version governance', () => {
  it('uses a project-free resource list and centered modal workflow', async () => {
    mockedGet.mockResolvedValue({ data: { data: [] } } as never);
    renderWithQuery(<DatasetsPage />);

    expect(resourcePageState.props.needsProject).toBe(false);
    await resourcePageState.props.queryFn(undefined, 1, 20);
    expect(mockedGet).toHaveBeenCalledWith('/v1/datasets', { params: { page: 1, size: 20 } });

    fireEvent.click(screen.getByRole('button', { name: /新建数据集/ }));
    const modal = await screen.findByRole('dialog');
    expect(modal.closest('.ant-modal-wrap')?.className).toContain('ant-modal-centered');
    await waitFor(() => expect(screen.getByLabelText('标签绑定选择器')).toBeTruthy());
    expect(screen.getByLabelText('未绑定标签')).toBeTruthy();
    expect(screen.getByLabelText('已绑定标签')).toBeTruthy();
  });

  it('submits the selected label order when creating a dataset', async () => {
    mockedGet.mockImplementation(async (path: string) => {
      if (path === '/v1/annotation-taxonomy/labels') {
        return { data: { data: [{ id: 18, label_key: '占道经营', label_value: 'street_vendor' }] } } as never;
      }
      return { data: { data: [] } } as never;
    });
    mockedPost.mockResolvedValue({ data: { data: { id: 9 } } } as never);
    renderWithQuery(<DatasetsPage />);

    fireEvent.click(screen.getByRole('button', { name: /新建数据集/ }));
    const labelCheckbox = await screen.findByRole('checkbox', { name: '选择未绑定标签 占道经营（street_vendor）' });
    fireEvent.click(labelCheckbox);
    fireEvent.click(screen.getByRole('button', { name: '绑定选中标签' }));
    fireEvent.change(screen.getByLabelText('数据集名称'), { target: { value: '占道经营数据集' } });
    fireEvent.click(screen.getByRole('button', { name: '创 建' }));

    await waitFor(() =>
      expect(mockedPost).toHaveBeenCalledWith('/v1/datasets', {
        name: '占道经营数据集',
        label_ids: [18],
      }),
    );
  });

  it('exposes a dataset-level label contract entry and opens its editor', async () => {
    mockedGet.mockImplementation(async (path: string) => {
      if (path.includes('/label-contracts/current')) return { data: { data: null } } as never;
      if (path === '/v1/annotation-taxonomy/labels') {
        return { data: { data: [] } } as never;
      }
      return { data: { data: [] } } as never;
    });

    renderWithQuery(<DatasetsPage />);

    const action = screen.getByTestId('dataset-label-contract-7');
    expect(action).toBeTruthy();
    expect(screen.getByRole('button', { name: '标签契约' })).toBe(action);

    fireEvent.click(action);
    await waitFor(() => expect(screen.getByRole('dialog')).toBeTruthy());
    expect(mockedGet).toHaveBeenCalledWith('/v1/datasets/7/label-contracts/current');
    await waitFor(() =>
      expect(mockedGet).toHaveBeenCalledWith('/v1/annotation-taxonomy/labels', {
        params: { include_disabled: false, page: 0, size: 20 },
      }),
    );
  });

  it('does not load taxonomy data until the label contract modal is opened', async () => {
    mockedGet.mockResolvedValue({ data: { data: [] } } as never);
    const view = renderWithQuery(
      <DatasetLabelContractModal dataset={{ id: 7, name: '占道经营' }} open={false} onClose={() => undefined} />,
    );
    await new Promise((resolve) => setTimeout(resolve, 25));
    expect(mockedGet).not.toHaveBeenCalled();

    view.unmount();
    renderWithQuery(<DatasetLabelContractModal dataset={{ id: 7, name: '占道经营' }} open onClose={() => undefined} />);
    await waitFor(() => expect(mockedGet).toHaveBeenCalled());
  });

  it('binds an existing dataset to the selected label contract from the dataset page', async () => {
    mockedGet.mockImplementation(async (path: string) => {
      if (path.includes('/label-contracts/current')) return { data: { data: null } } as never;
      if (path === '/v1/annotation-taxonomy/labels')
        return {
          data: {
            data: [{ id: 18, label_key: '占道经营', label_value: 'street_vendor' }],
          },
        } as never;
      return { data: { data: [] } } as never;
    });
    mockedPost.mockResolvedValue({ data: { data: { id: 31, revision: 1, status: 'DRAFT' } } } as never);
    const onClose = vi.fn();
    renderWithQuery(<DatasetLabelContractModal dataset={{ id: 7, name: '占道经营' }} open onClose={onClose} />);

    await waitFor(() => expect(screen.getByText('数据集标签（按顺序）')).toBeTruthy());
    const labelCheckbox = await screen.findByRole('checkbox', { name: '选择未绑定标签 占道经营（street_vendor）' });
    fireEvent.click(labelCheckbox);
    fireEvent.click(screen.getByRole('button', { name: '绑定选中标签' }));
    fireEvent.click(screen.getByRole('button', { name: '确 定' }));

    await waitFor(() =>
      expect(mockedPost).toHaveBeenCalledWith('/v1/datasets/7/label-contracts', {
        label_ids: [18],
      }),
    );
    expect(onClose).toHaveBeenCalled();
  });

  it('opens contract-bound AI prompt editor and generates a prompt preview', async () => {
    mockedGet.mockImplementation(async (path: string) => {
      if (path.includes('/label-contracts/current')) {
        return {
          data: {
            data: {
              id: 41,
              dataset_id: 7,
              revision: 3,
              annotation_kind: 'bbox',
              label_scope_mode: 'FULL',
              label_ids: [18],
              labels: [{ label_id: 18, label_key: '行人', label_value: 'person' }],
              label_set_signature: 'labels-signature',
              class_order_signature: 'order-signature',
              status: 'ACTIVE',
            },
          },
        } as never;
      }
      if (path.includes('/ai-prompts')) return { data: { data: [] } } as never;
      if (path === '/v1/annotation-taxonomy/labels') return { data: { data: [] } } as never;
      return { data: { data: [] } } as never;
    });
    mockedPost.mockImplementation(async (path: string) => {
      if (path.endsWith('/ai-prompts/preview')) {
        return {
          data: {
            data: {
              generated_prompt: '只输出契约内的 person 框。',
              effective_prompt: '只输出契约内的 person 框。',
              warnings: [],
              profile_key: 'annotation_bbox_v1',
            },
          },
        } as never;
      }
      return { data: { data: { id: 91, status: 'DRAFT', effective_prompt: '草稿' } } } as never;
    });

    renderWithQuery(<DatasetsPage />);
    fireEvent.click(screen.getByTestId('dataset-label-contract-7'));
    await waitFor(() => expect(screen.getByRole('button', { name: 'AI提示词' })).toBeTruthy());
    fireEvent.click(screen.getByRole('button', { name: 'AI提示词' }));

    await screen.findByLabelText('AI提示词编辑器');
    expect(await screen.findByText('person')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: '根据契约生成' }));

    await waitFor(() =>
      expect(mockedPost).toHaveBeenCalledWith(
        '/v1/datasets/7/label-contracts/3/ai-prompts/preview',
        expect.objectContaining({ operation: 'bbox_detection', provider_key: 'volcengine' }),
      ),
    );
    expect(await screen.findByDisplayValue('只输出契约内的 person 框。')).toBeTruthy();
  });

  it('edits and saves a contract prompt, gates publish while dirty, then publishes the saved draft', async () => {
    const contract = {
      id: 41,
      dataset_id: 7,
      revision: 3,
      annotation_kind: 'bbox',
      label_scope_mode: 'FULL',
      labels: [{ label_id: 18, label_key: '行人', label_value: 'person' }],
      label_set_signature: 'labels-signature',
      class_order_signature: 'order-signature',
      status: 'ACTIVE',
    };
    let promptRows: any[] = [];
    const generatedPrompt = '只输出契约内的 person 框。';
    const editedPrompt = `${generatedPrompt}\n补充边界规则。`;
    const savedPrompt = {
      id: 91,
      dataset_id: 7,
      contract_id: 41,
      contract_revision: 3,
      status: 'DRAFT',
      effective_prompt: editedPrompt,
      edited_prompt: editedPrompt,
      generated_prompt: generatedPrompt,
      prompt_sha256: 'sha-draft',
      label_set_signature: 'labels-signature',
      class_order_signature: 'order-signature',
    };
    mockedGet.mockImplementation(async (path: string) => {
      if (path.includes('/label-contracts/current')) return { data: { data: contract } } as never;
      if (path.includes('/ai-prompts')) return { data: { data: promptRows } } as never;
      return { data: { data: [] } } as never;
    });
    mockedPost.mockImplementation(async (path: string, payload?: any) => {
      if (path.endsWith('/ai-prompts/preview')) {
        const preview = {
          ...contract,
          id: undefined,
          contract_id: contract.id,
          contract_revision: contract.revision,
          operation: 'bbox_detection',
          provider_key: 'volcengine',
          profile_key: 'annotation_bbox_v1',
          effective_prompt: generatedPrompt,
          generated_prompt: generatedPrompt,
          status: 'PREVIEW',
          warnings: [],
        };
        return { data: { data: preview } } as never;
      }
      if (path.endsWith('/ai-prompts')) {
        promptRows = [savedPrompt];
        return { data: { data: savedPrompt } } as never;
      }
      if (path.endsWith('/publish')) {
        const published = { ...savedPrompt, status: 'PUBLISHED', published_at: '2026-10-27T00:00:00' };
        promptRows = [published];
        return { data: { data: published } } as never;
      }
      return { data: { data: {} } } as never;
    });

    renderWithQuery(
      <AnnotationPromptModal
        dataset={{ id: 7, name: '道路数据' }}
        contract={contract}
        open
        onClose={() => undefined}
      />,
    );

    const editor = await screen.findByLabelText('AI提示词编辑器');
    await waitFor(() =>
      expect(mockedGet).toHaveBeenCalledWith('/v1/datasets/7/label-contracts/3/ai-prompts'),
    );
    fireEvent.click(screen.getByRole('button', { name: '根据契约生成' }));
    await waitFor(() => expect(screen.getByDisplayValue(generatedPrompt)).toBeTruthy());
    fireEvent.change(editor, { target: { value: editedPrompt } });
    expect((screen.getByRole('button', { name: '发布' }) as HTMLButtonElement).disabled).toBe(true);

    fireEvent.click(screen.getByRole('button', { name: '保存草稿' }));
    await waitFor(() =>
      expect(mockedPost).toHaveBeenCalledWith(
        '/v1/datasets/7/label-contracts/3/ai-prompts',
        expect.objectContaining({
          effective_prompt: editedPrompt,
          expected_label_set_signature: 'labels-signature',
          expected_class_order_signature: 'order-signature',
          expected_prompt_id: null,
        }),
      ),
    );
    await waitFor(() => expect((editor as HTMLTextAreaElement).value).toBe(editedPrompt));

    fireEvent.change(editor, { target: { value: `${editedPrompt}\n未保存修改` } });
    expect((screen.getByRole('button', { name: '发布' }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(editor, { target: { value: editedPrompt } });
    await waitFor(() => expect((screen.getByRole('button', { name: '发布' }) as HTMLButtonElement).disabled).toBe(false));
    const modelInput = screen.getByLabelText('模型名称') as HTMLInputElement;
    fireEvent.change(modelInput, { target: { value: 'doubao-vision' } });
    expect((screen.getByRole('button', { name: '发布' }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(modelInput, { target: { value: '' } });
    await waitFor(() => expect((screen.getByRole('button', { name: '发布' }) as HTMLButtonElement).disabled).toBe(false));
    const profileInput = screen.getByLabelText('提示词模板') as HTMLInputElement;
    fireEvent.change(profileInput, { target: { value: 'custom_profile.v2' } });
    expect((screen.getByRole('button', { name: '发布' }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(profileInput, { target: { value: 'annotation_bbox_v1' } });
    await waitFor(() => expect((screen.getByRole('button', { name: '发布' }) as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(screen.getByRole('button', { name: '发布' }));
    await waitFor(() =>
      expect(mockedPost).toHaveBeenCalledWith(
        '/v1/datasets/7/label-contracts/3/ai-prompts/91/publish',
        { expected_contract_revision: 3, expected_prompt_sha256: 'sha-draft' },
      ),
    );
    expect(await screen.findByText('当前生效')).toBeTruthy();
  });

  it('keeps a newly saved draft selected when an older published prompt is also present', async () => {
    const contract = {
      id: 41,
      dataset_id: 7,
      revision: 3,
      annotation_kind: 'bbox',
      label_scope_mode: 'FULL',
      labels: [{ label_id: 18, label_key: '行人', label_value: 'person' }],
      label_set_signature: 'labels-signature',
      class_order_signature: 'order-signature',
      status: 'ACTIVE',
    };
    const publishedPrompt = {
      id: 90,
      dataset_id: 7,
      contract_id: 41,
      contract_revision: 3,
      status: 'PUBLISHED',
      effective_prompt: '旧版 person 提示词。',
      generated_prompt: '旧版 person 提示词。',
      model_name: null,
      profile_key: 'annotation_bbox_v1',
      prompt_sha256: 'sha-published',
      label_set_signature: 'labels-signature',
      class_order_signature: 'order-signature',
    };
    const draftPrompt = {
      id: 91,
      dataset_id: 7,
      contract_id: 41,
      contract_revision: 3,
      status: 'DRAFT',
      effective_prompt: '新版 person 提示词。',
      edited_prompt: '新版 person 提示词。',
      generated_prompt: '旧版 person 提示词。',
      model_name: null,
      profile_key: 'annotation_bbox_v1',
      prompt_sha256: 'sha-draft',
      label_set_signature: 'labels-signature',
      class_order_signature: 'order-signature',
    };
    let promptRows: any[] = [publishedPrompt];
    mockedGet.mockImplementation(async (path: string) => {
      if (path.includes('/label-contracts/current')) return { data: { data: contract } } as never;
      if (path.includes('/ai-prompts')) return { data: { data: promptRows } } as never;
      return { data: { data: [] } } as never;
    });
    mockedPost.mockImplementation(async (path: string) => {
      if (path.endsWith('/ai-prompts')) {
        promptRows = [publishedPrompt, draftPrompt];
        return { data: { data: draftPrompt } } as never;
      }
      if (path.endsWith('/publish')) {
        return { data: { data: { ...draftPrompt, status: 'PUBLISHED' } } } as never;
      }
      return { data: { data: {} } } as never;
    });

    renderWithQuery(
      <AnnotationPromptModal
        dataset={{ id: 7, name: '道路数据' }}
        contract={contract}
        open
        onClose={() => undefined}
      />,
    );

    const editor = await screen.findByLabelText('AI提示词编辑器');
    await waitFor(() => expect((editor as HTMLTextAreaElement).value).toBe(publishedPrompt.effective_prompt));
    fireEvent.change(editor, { target: { value: draftPrompt.effective_prompt } });
    fireEvent.click(screen.getByRole('button', { name: '保存草稿' }));

    await waitFor(() =>
      expect(mockedPost).toHaveBeenCalledWith(
        '/v1/datasets/7/label-contracts/3/ai-prompts',
        expect.objectContaining({ effective_prompt: draftPrompt.effective_prompt, expected_prompt_id: null }),
      ),
    );
    await waitFor(() => expect((editor as HTMLTextAreaElement).value).toBe(draftPrompt.effective_prompt));
    expect((screen.getByRole('button', { name: '发布' }) as HTMLButtonElement).disabled).toBe(false);
    fireEvent.click(screen.getByRole('button', { name: '发布' }));
    await waitFor(() =>
      expect(mockedPost).toHaveBeenCalledWith(
        '/v1/datasets/7/label-contracts/3/ai-prompts/91/publish',
        expect.objectContaining({ expected_prompt_sha256: 'sha-draft' }),
      ),
    );
  });

  it('resets model and prompt profile when the contract prompt modal is reopened', async () => {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const contract = {
      id: 41,
      revision: 3,
      annotation_kind: 'bbox',
      labels: [{ label_id: 18, label_key: '行人', label_value: 'person' }],
      label_set_signature: 'labels-signature',
      class_order_signature: 'order-signature',
    };
    mockedGet.mockImplementation(async (path: string) => {
      if (path.includes('/label-contracts/current')) return { data: { data: contract } } as never;
      return { data: { data: [] } } as never;
    });
    const view = render(
      <QueryClientProvider client={queryClient}>
        <AnnotationPromptModal dataset={{ id: 7, name: '道路数据' }} contract={contract} open onClose={() => undefined} />
      </QueryClientProvider>,
    );

    const modelInput = await screen.findByLabelText('模型名称');
    const profileInput = screen.getByLabelText('提示词模板');
    await waitFor(() =>
      expect(mockedGet).toHaveBeenCalledWith('/v1/datasets/7/label-contracts/3/ai-prompts'),
    );
    fireEvent.change(modelInput, { target: { value: 'previous-model' } });
    fireEvent.change(profileInput, { target: { value: 'previous-profile.v1' } });
    expect((modelInput as HTMLInputElement).value).toBe('previous-model');
    expect((profileInput as HTMLInputElement).value).toBe('previous-profile.v1');

    view.rerender(
      <QueryClientProvider client={queryClient}>
        <AnnotationPromptModal dataset={{ id: 7, name: '道路数据' }} contract={contract} open={false} onClose={() => undefined} />
      </QueryClientProvider>,
    );
    view.rerender(
      <QueryClientProvider client={queryClient}>
        <AnnotationPromptModal dataset={{ id: 7, name: '道路数据' }} contract={contract} open onClose={() => undefined} />
      </QueryClientProvider>,
    );

    await waitFor(() => {
      expect((screen.getByLabelText('模型名称') as HTMLInputElement).value).toBe('');
      expect((screen.getByLabelText('提示词模板') as HTMLInputElement).value).toBe('annotation_bbox_v1');
    });
  });

  it('restores the latest bbox draft when the prompt modal is reopened', async () => {
    const contract = {
      id: 41,
      revision: 3,
      annotation_kind: 'bbox',
      labels: [{ label_id: 18, label_key: '行人', label_value: 'person' }],
      label_set_signature: 'labels-signature',
      class_order_signature: 'order-signature',
    };
    const publishedPrompt = {
      id: 90,
      contract_id: 41,
      contract_revision: 3,
      operation: 'bbox_detection',
      status: 'PUBLISHED',
      effective_prompt: '已发布 person 提示词。',
      model_name: null,
      profile_key: 'annotation_bbox_v1',
    };
    const draftPrompt = {
      id: 91,
      contract_id: 41,
      contract_revision: 3,
      operation: 'bbox_detection',
      status: 'DRAFT',
      effective_prompt: '待发布 person 草稿。',
      model_name: 'doubao-vision',
      profile_key: 'custom_bbox_v2',
    };
    mockedGet.mockImplementation(async (path: string) => {
      if (path.includes('/label-contracts/current')) return { data: { data: contract } } as never;
      return { data: { data: [publishedPrompt, draftPrompt] } } as never;
    });
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const view = render(
      <QueryClientProvider client={queryClient}>
        <AnnotationPromptModal dataset={{ id: 7, name: '道路数据' }} contract={contract} open onClose={() => undefined} />
      </QueryClientProvider>,
    );

    const editor = await screen.findByLabelText('AI提示词编辑器');
    await waitFor(() => expect((editor as HTMLTextAreaElement).value).toBe(draftPrompt.effective_prompt));
    expect((screen.getByLabelText('模型名称') as HTMLInputElement).value).toBe(draftPrompt.model_name);
    expect((screen.getByLabelText('提示词模板') as HTMLInputElement).value).toBe(draftPrompt.profile_key);

    view.rerender(
      <QueryClientProvider client={queryClient}>
        <AnnotationPromptModal dataset={{ id: 7, name: '道路数据' }} contract={contract} open={false} onClose={() => undefined} />
      </QueryClientProvider>,
    );
    view.rerender(
      <QueryClientProvider client={queryClient}>
        <AnnotationPromptModal dataset={{ id: 7, name: '道路数据' }} contract={contract} open onClose={() => undefined} />
      </QueryClientProvider>,
    );

    await waitFor(() => expect((screen.getByLabelText('AI提示词编辑器') as HTMLTextAreaElement).value).toBe(draftPrompt.effective_prompt));
    expect(screen.queryByDisplayValue(publishedPrompt.effective_prompt)).toBeNull();
  });

  it('does not show a global-tag binding in the bbox prompt editor', async () => {
    mockedGet.mockImplementation(async (path: string) => {
      if (path.includes('/label-contracts/current')) {
        return {
          data: {
            data: {
              id: 41,
              dataset_id: 7,
              revision: 3,
              annotation_kind: 'bbox',
              labels: [{ label_id: 18, label_key: '行人', label_value: 'person' }],
            },
          },
        } as never;
      }
      return {
        data: {
          data: [
            { id: 99, operation: 'global_tag', status: 'PUBLISHED', effective_prompt: '全局标签提示词' },
            { id: 100, operation: 'bbox_detection', status: 'DRAFT', effective_prompt: '目标框提示词' },
          ],
        },
      } as never;
    });
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={queryClient}>
        <AnnotationPromptModal
          dataset={{ id: 7, name: '道路数据' }}
          contract={{
            id: 41,
            revision: 3,
            annotation_kind: 'bbox',
            labels: [{ label_id: 18, label_key: '行人', label_value: 'person' }],
          }}
          open
          onClose={() => undefined}
        />
      </QueryClientProvider>,
    );

    expect(await screen.findByDisplayValue('目标框提示词')).toBeTruthy();
    expect(screen.queryByDisplayValue('全局标签提示词')).toBeNull();
  });

  it('loads the dataset list for the current user without a project parameter', async () => {
    mockedGet.mockResolvedValue({ data: { data: [] } } as never);
    await fetchDatasets(2, 50);
    expect(mockedGet).toHaveBeenCalledWith('/v1/datasets', { params: { page: 2, size: 50 } });
  });

  it('passes the trimmed search keyword to the server dataset query', async () => {
    mockedGet.mockResolvedValue({ data: { data: [] } } as never);
    await fetchDatasets(0, 20, '  Road traffic  ');
    expect(mockedGet).toHaveBeenCalledWith('/v1/datasets', {
      params: { page: 0, size: 20, q: 'Road traffic' },
    });
  });

  it('wires the embedded dataset list search to ResourcePage', async () => {
    mockedGet.mockResolvedValue({ data: { data: [] } } as never);
    renderWithQuery(<DatasetsPage embedded />);
    expect(resourcePageState.props.search).toBe('');
    expect(resourcePageState.props.searchPlaceholder).toContain('中文');
    await resourcePageState.props.queryFn(undefined, 0, 20, 'vehicle');
    expect(mockedGet).toHaveBeenCalledWith('/v1/datasets', {
      params: { page: 0, size: 20, q: 'vehicle' },
    });
  });

  it('loads finished source tasks by dataset and current-user task access', async () => {
    mockedGet.mockResolvedValue({ data: { data: [] } } as never);
    await fetchSourceTasks(1, 2, 100);
    expect(mockedGet).toHaveBeenCalledWith('/v1/tasks', {
      params: { dataset_id: 1, page: 2, size: 100, status: 'FINISHED' },
    });
  });

  it('passes a trimmed source-task search to the server without changing the finished-task scope', async () => {
    mockedGet.mockResolvedValue({ data: { data: [], meta_data: { total: 0, page: 0, size: 20 } } } as never);
    await fetchSourceTasks(7, 0, 20, '  road-camera  ');
    expect(mockedGet).toHaveBeenCalledWith('/v1/tasks', {
      params: { dataset_id: 7, page: 0, q: 'road-camera', size: 20, status: 'FINISHED' },
    });
  });

  it('keeps source-task selections when the server-backed picker changes page', async () => {
    mockedGet.mockImplementation(async (path: string, config?: any) => {
      if (path !== '/v1/tasks') return { data: { data: [] } } as never;
      const page = Number(config?.params?.page || 0);
      const rows = page === 0
        ? [{ id: 31, name: '第一页任务', status: 'FINISHED', media_type: 'IMAGE' }]
        : [{ id: 32, name: '第二页任务', status: 'FINISHED', media_type: 'VIDEO' }];
      return { data: { data: rows, meta_data: { total: 21, page, size: 20 } } } as never;
    });
    mockedPost.mockResolvedValue({ data: { data: { id: 21 } } } as never);
    const onClose = vi.fn();
    renderWithQuery(
      <CreateVersionModal
        dataset={{ id: 7, name: '道路数据' }}
        open
        onClose={onClose}
        onCreated={() => undefined}
      />,
    );

    const select = await screen.findByLabelText('来源标注任务');
    fireEvent.mouseDown(select);
    await waitFor(() => expect(screen.getByText('第一页任务 · IMAGE · 已完成')).toBeTruthy());
    fireEvent.click(screen.getByText('第一页任务 · IMAGE · 已完成'));

    fireEvent.click(screen.getByTitle('2'));
    await waitFor(() =>
      expect(mockedGet).toHaveBeenCalledWith('/v1/tasks', {
        params: { dataset_id: 7, page: 1, size: 20, status: 'FINISHED' },
      }),
    );
    fireEvent.mouseDown(screen.getByLabelText('来源标注任务'));
    await waitFor(() => expect(screen.getByText('第二页任务 · VIDEO · 已完成')).toBeTruthy());
    fireEvent.click(screen.getByText('第二页任务 · VIDEO · 已完成'));
    fireEvent.click(screen.getByRole('button', { name: '生成训练版本' }));

    await waitFor(() =>
      expect(mockedPost).toHaveBeenCalledWith(
        '/v1/datasets/7/versions/from-tasks',
        expect.objectContaining({ source_task_ids: [31, 32] }),
      ),
    );
  });

  it('maps lifecycle statuses and rejects non-object metadata', () => {
    expect(datasetVersionStatus('FROZEN')).toMatchObject({ label: '已冻结', color: 'green' });
    expect(parseDatasetMetadata('{"source":"manual"}')).toEqual({ source: 'manual' });
    expect(() => parseDatasetMetadata('[1]')).toThrow('元数据必须是 JSON 对象');
  });

  it('loads versions, validates the selected version, and only enables freeze after a passing check', async () => {
    mockedGet.mockImplementation(async (path: string) => {
      if (path === '/v1/datasets/7/versions')
        return {
          data: {
            data: [
              {
                id: 11,
                dataset_id: 7,
                version_no: 1,
                status: 'READY_TO_REVIEW',
                review_status: 'APPROVED',
                manifest_rel_path: 'manifest.json',
                manifest_sha256: 'a'.repeat(64),
                sample_count: 12,
              },
            ],
          },
        } as never;
      return {
        data: { data: { version_id: 11, valid: true, checks: { manifest_exists: true }, warnings: [] } },
      } as never;
    });
    mockedPost.mockResolvedValue({ data: { data: { id: 11, status: 'FROZEN' } } } as never);
    renderWithQuery(<VersionGovernanceModal dataset={{ id: 7, name: '道路数据' }} open onClose={() => undefined} />);
    await waitFor(() => expect(screen.getByText('v1 版本控制')).toBeTruthy());
    expect(mockedGet).toHaveBeenCalledWith('/v1/datasets/7/versions', { params: { page: 0, size: 20 } });
    const freeze = screen.getByText('冻结版本').closest('button') as HTMLButtonElement;
    expect(freeze?.disabled).toBe(true);
    fireEvent.click(screen.getByText('执行校验').closest('button') as HTMLButtonElement);
    await waitFor(() => expect(screen.getByText('校验通过，可冻结')).toBeTruthy());
    expect(freeze?.disabled).toBe(false);
    fireEvent.click(freeze);
    fireEvent.click(screen.getByRole('button', { name: '确认冻结' }));
    await waitFor(() => expect(mockedPost).toHaveBeenCalledWith('/v1/dataset-versions/11/freeze'));
  });

  it('keeps the responsive version details free of Descriptions span warnings', async () => {
    mockedGet.mockResolvedValue({
      data: {
        data: [
          {
            id: 11,
            dataset_id: 7,
            version_no: 1,
            status: 'FROZEN',
            manifest_rel_path: 'manifest.json',
            manifest_sha256: 'a'.repeat(64),
            sample_count: 12,
          },
        ],
      },
    } as never);
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => undefined);
    try {
      renderWithQuery(<VersionGovernanceModal dataset={{ id: 7, name: '道路数据' }} open onClose={() => undefined} />);
      await waitFor(() => expect(screen.getByText('v1 版本控制')).toBeTruthy());
      expect(consoleError.mock.calls.flat().join(' ')).not.toContain(
        'Sum of column `span` in a line not match `column`',
      );
    } finally {
      consoleError.mockRestore();
    }
  });

  it('generates a version from completed tasks without exposing manifest fields', async () => {
    mockedGet.mockImplementation(async (path: string) => {
      if (path === '/v1/tasks') {
        return {
          data: {
            data: [{ id: 31, name: '占道经营标注任务', status: 'FINISHED', media_type: 'IMAGE' }],
          },
        } as never;
      }
      return { data: { data: [] } } as never;
    });
    mockedPost.mockResolvedValue({ data: { data: { id: 21 } } } as never);
    const onClose = vi.fn();
    const onCreated = vi.fn();
    renderWithQuery(
      <CreateVersionModal
        dataset={{ id: 7, name: '道路数据' }}
        open
        onClose={onClose}
        onCreated={onCreated}
      />,
    );
    fireEvent.mouseDown(screen.getByLabelText('来源标注任务'));
    await waitFor(() => expect(screen.getByText('占道经营标注任务 · IMAGE · 已完成')).toBeTruthy());
    fireEvent.click(screen.getByText('占道经营标注任务 · IMAGE · 已完成'));
    fireEvent.click(screen.getByRole('button', { name: '生成训练版本' }));
    await waitFor(() =>
      expect(mockedPost).toHaveBeenCalledWith(
        '/v1/datasets/7/versions/from-tasks',
        expect.objectContaining({
          source_task_ids: [31],
          train_ratio: 0.8,
          val_ratio: 0.1,
          test_ratio: 0.1,
        }),
      ),
    );
    expect(onClose).toHaveBeenCalled();
    expect(onCreated).toHaveBeenCalled();
  });
});
