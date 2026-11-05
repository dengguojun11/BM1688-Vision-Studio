import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';

import request from '@/api/request';
import { trainingQualityGateSummary, trainingRuntimeStatusLabel, trainingStatusLabel } from '../shared';
import { trainingModelVersionLabel } from './training';
import TrainingPage, { autoTrainingPlanStatusLabel, evaluationHubUrl, isPlateTrainingTarget, isRemoteNodeMissing, isUnsupportedGenericExecution, legacyTrainingJobUrl, mergeTrainingFormValues, parseTrainingRecipeRef, partitionAiIterationParams, pinIterationWeights, platformTrainingJobUrl, platformTrainingReportsUrl, PRECISION_OPTIONS, precisionOptionLabel, resolveDefaultTrainingContract, resolveTrainingTargetContract, shouldUseLegacyYoloCreate, TARGET_CHIP_OPTIONS, TRAINING_IMPORTED_MODEL_PAGE_SIZE, TRAINING_LABEL_PAGE_SIZE, trainingCandidateBrowserResult, trainingCandidateCanTrain, trainingCandidateContractKey, trainingCandidateRatioLabel, trainingCandidatesShareLabelContract, trainingCandidateSelectionSnapshotKey, trainingCandidateSnapshotKey, trainingCandidateState, trainingCandidateStateLabel, trainingCandidateStats, trainingCandidateQueryParams, trainingCandidateQueryParamsForDimension, trainingChipOptions, trainingCreateErrorMessage, trainingDatasetBrowserResult, trainingDatasetContractLabel, trainingDatasetDisplayLabel, trainingDatasetLabelCount, trainingDatasetLabelsForSelection, trainingDatasetSearchLabel, trainingDatasetVersionLabel, trainingDimensionLabel, trainingEntryParamsFromLocation, trainingGlobalTagDimensionBrowserResult, trainingImportedModelBrowserResult, trainingIterationParentLabel, trainingIterationPolicyLabel, trainingLabelBrowserResult, trainingLabelIdsForCandidates, trainingModelVersionCompatibility, trainingModelVersionWeightFile, trainingProgressSummary, trainingSampleConditionsJson, trainingSampleConditionsKey, trainingTargetFromRecipe, trainingTargetOptions, trainingViewFromLocation, taskTrainingDatasetSelectionError, taskTrainingUsesMergedEndpoint } from './training';

const taskSuperuserState = vi.hoisted(() => ({ value: true, fetched: true }));
const resourcePageState = vi.hoisted(() => ({ props: null as any }));

vi.mock('@/api/request', () => ({
  default: { get: vi.fn(), post: vi.fn() },
}));

vi.mock('@/hooks/useTaskSuperuser', () => ({
  default: () => ({ isTaskSuperuser: taskSuperuserState.value, isFetched: taskSuperuserState.fetched }),
}));

vi.mock('../ResourcePage', () => ({
  default: (props: any) => {
    resourcePageState.props = props;
    return (
      <div>
        {props.beforeTable}
        <div data-testid="row-actions">{props.rowActions?.({ id: 42, task_id: 7, status: 'PROCESSING', name: 'job-42' })}</div>
        <div data-testid="failed-row-actions">{props.rowActions?.({ id: 43, task_id: 7, status: 'FAILED', name: 'job-43', is_restartable: true })}</div>
        <div data-testid="completed-row-actions">{props.rowActions?.({ id: 44, task_id: 7, status: 'COMPLETED', name: 'job-44' })}</div>
        <div data-testid="cancelled-row-actions">{props.rowActions?.({ id: 45, task_id: 7, status: 'CANCELLED', name: 'job-45' })}</div>
      </div>
    );
  },
}));

const mockedGet = vi.mocked(request.get);
const mockedPost = vi.mocked(request.post);

if (!window.matchMedia) {
  window.matchMedia = ((query: string) => ({ matches: false, media: query, onchange: null, addListener: () => undefined, removeListener: () => undefined, addEventListener: () => undefined, removeEventListener: () => undefined, dispatchEvent: () => false })) as typeof window.matchMedia;
}

afterEach(() => {
  cleanup();
  mockedGet.mockReset();
  mockedPost.mockReset();
  taskSuperuserState.value = true;
  taskSuperuserState.fetched = true;
  resourcePageState.props = null;
  localStorage.removeItem('token');
  window.history.replaceState({}, '', '/training');
});

function renderPage() {
  localStorage.setItem('token', 'training-test-token');
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<MemoryRouter initialEntries={[window.location.pathname + window.location.search]}><QueryClientProvider client={queryClient}><TrainingPage /></QueryClientProvider></MemoryRouter>);
}

describe('TrainingPage', () => {
  it('hides historical automatic plans from annotation users', async () => {
    taskSuperuserState.value = false;
    window.history.replaceState({}, '', '/training?view=auto-plan');

    renderPage();

    expect(screen.queryByRole('tab', { name: '历史自动训练计划' })).toBeNull();
    await waitFor(() => expect(screen.getByRole('tab', { name: '训练作业' }).getAttribute('aria-selected')).toBe('true'));
    await waitFor(() => expect(window.location.search).toBe(''));
  });

  it('keeps legacy plan URLs readable while the current-user permission is loading', () => {
    taskSuperuserState.fetched = false;
    window.history.replaceState({}, '', '/training?view=auto-plan');

    renderPage();

    expect(screen.queryByRole('tab', { name: '历史自动训练计划' })).toBeNull();
    expect(screen.getByText('历史自动训练计划')).toBeTruthy();
    expect(window.location.search).toBe('?view=auto-plan');
  });

  it('exposes only the supported edge chip targets', () => {
    expect(TARGET_CHIP_OPTIONS.map((item) => item.value)).toEqual(['bm1688', 'bm1684', 'rk1808']);
    expect(trainingChipOptions({ supported_chips: ['cpu', 'cuda', 'rk3588'] }).map((item) => item.value)).toEqual([
      'bm1688',
      'bm1684',
      'rk1808',
    ]);
    expect(trainingChipOptions({ supported_chips: ['bm1684'] }).map((item) => item.value)).toEqual(['bm1684']);
  });

  it('exposes complete precision labels for the edge target selector', () => {
    expect(PRECISION_OPTIONS).toEqual([
      { value: 'fp32', label: 'FP32 · 全精度' },
      { value: 'fp16', label: 'FP16 · 半精度' },
      { value: 'int8', label: 'INT8 · 8 位量化' },
    ]);
    expect(precisionOptionLabel('FP16')).toBe('FP16 · 半精度');
    expect(precisionOptionLabel('unknown')).toBe('');
  });

  it('keeps task and label training as explicit independent dimensions', () => {
    expect(trainingDimensionLabel('task')).toBe('按任务训练');
    expect(trainingDimensionLabel('label')).toBe('按标签训练');
  });

  it('builds a readable training dataset summary for the selector', () => {
    const dataset = {
      id: 1,
      name: '占道经营',
      dataset_category_name: '城市治理',
      dataset_label_contract_revision: 2,
      dataset_label_ids: [3, 3, 5],
    };
    expect(trainingDatasetLabelCount(dataset)).toBe(2);
    expect(trainingDatasetDisplayLabel(dataset)).toBe('占道经营 #1');
    expect(trainingDatasetDisplayLabel({ id: 2, name: '占道经营' })).toBe('占道经营 #2');
    expect(trainingDatasetContractLabel(dataset)).toBe('标签契约 v2 · 2 个标签');
    expect(trainingDatasetVersionLabel({ id: 10, dataset_id: 1, version_no: 4, sample_count: 1628 })).toBe('已有冻结版本 v4 · 1,628 个样本');
    expect(trainingDatasetSearchLabel(dataset, { id: 10, dataset_id: 1, version_no: 4, sample_count: 1628 })).toBe(
      '占道经营 · 标签契约 v2 · 2 个标签 · 已有冻结版本 v4 · 1,628 个样本',
    );
    expect(trainingDatasetContractLabel({ id: 2, name: '非机动车违停' })).toBe('按任务契约自动固化');
    expect(trainingDatasetVersionLabel(undefined, { id: 11, dataset_id: 2, version_no: 1, sample_count: 20 })).toBe('已有待治理版本 v1 · 20 个样本（训练时会生成新快照）');
    expect(trainingDatasetVersionLabel()).toBe('首次训练时自动生成冻结快照');
  });

  it('unions labels from every selected dataset while preserving contract order', () => {
    expect(trainingDatasetLabelsForSelection([
      {
        id: 1,
        name: '白天',
        dataset_labels: [
          { id: 11, label_key: 'car', label_value: '车辆' },
          { id: 12, label_key: 'person', label_value: '行人' },
        ],
      },
      {
        id: 2,
        name: '夜间',
        dataset_labels: [
          { id: 12, label_key: 'person', label_value: '行人' },
          { label_id: 13, id: 13, label_key: 'bike', label_value: '自行车' },
        ],
      },
    ], [1, 2])).toEqual([
      { id: 11, label_key: 'car', label_value: '车辆' },
      { id: 12, label_key: 'person', label_value: '行人' },
      { id: 13, label_id: 13, label_key: 'bike', label_value: '自行车' },
    ]);
    expect(trainingDatasetLabelsForSelection([
      { id: 1, name: '白天', dataset_labels: [{ id: 11, label_key: 'car', label_value: '车辆' }] },
    ], [2])).toEqual([]);
  });

  it('keeps server-side dataset pagination metadata for the large selector', () => {
    expect(trainingDatasetBrowserResult({
      data: [{ id: 17, name: '道路检测' }],
      meta_data: { total: 501, page: 2, size: 20 },
    })).toEqual({
      items: [{ id: 17, name: '道路检测' }],
      total: 501,
      page: 2,
      size: 20,
    });
    expect(trainingDatasetBrowserResult({ data: [] })).toMatchObject({ total: 0, page: 0 });
  });

  it('keeps server-side label pagination metadata for the target-label selector', () => {
    expect(trainingLabelBrowserResult({
      data: [{ id: 17, label_key: 'vehicle', label_value: '车辆' }],
      meta_data: { total: 501, page: 1, size: 1 },
    })).toEqual({
      items: [{ id: 17, label_key: 'vehicle', label_value: '车辆' }],
      total: 501,
      page: 1,
      size: 1,
    });
    expect(TRAINING_LABEL_PAGE_SIZE).toBe(50);
  });

  it('parses global tool-tag dimensions and keeps condition keys deterministic', () => {
    expect(trainingGlobalTagDimensionBrowserResult({
      data: [{
        id: 1,
        field_key: 'weather',
        name_cn: '天气环境',
        options: [{ id: 2, name_cn: '雨天', value_en: 'rain' }],
      }],
      meta_data: { total: 3, page: 0, size: 3 },
    })).toMatchObject({
      total: 3,
      items: [{ field_key: 'weather', options: [{ value_en: 'rain' }] }],
    });
    expect(trainingSampleConditionsKey({
      camera_angle: ['俯视'],
      weather: ['rain', 'rain', 'fog'],
    })).toBe('camera_angle:俯视|weather:fog,rain');
    expect(trainingSampleConditionsKey({ weather: [] })).toBe('');
  });

  it('keeps server-side imported-model pagination metadata', () => {
    expect(trainingImportedModelBrowserResult({
      data: {
        data: [{ id: 9, display_name: '夜间检测模型' }],
        meta_data: { total: 41, page: 2, size: 20 },
      },
    })).toEqual({
      items: [{ id: 9, display_name: '夜间检测模型' }],
      total: 41,
      page: 2,
      size: 20,
    });
    expect(TRAINING_IMPORTED_MODEL_PAGE_SIZE).toBe(20);
  });

  it('explains the automatic iteration selection policy', () => {
    expect(trainingIterationPolicyLabel({ weights_source: 'trained', model_weights: 'trained:2', selection_policy: 'auto_prefer_selected_tasks_then_latest', suggested_params: {} })).toContain('优先所选来源任务');
    expect(trainingIterationPolicyLabel({ weights_source: 'preset', model_weights: 'yolo11n.pt', selection_policy: 'auto_compatible_parent', suggested_params: {} })).toContain('兼容父模型');
  });

  it('shows the concrete PT artifact filename when a model version provides one', () => {
    expect(trainingModelVersionWeightFile({
      id: 9,
      version_no: 2,
      display_name: '人体识别 · v2',
      artifacts: [{ artifact_type: 'pt', relative_path: 'yolo/9/best.pt', status: 'READY' }],
    })).toBe('best.pt');
    expect(trainingModelVersionWeightFile({
      id: 10,
      version_no: 1,
      display_name: '历史模型',
      manifest: { weights_path: 'legacy/last.pt' },
    })).toBe('last.pt');
    expect(trainingModelVersionWeightFile()).toBe('待预检确认');
  });

  it('prefers the branch-local version and falls back to the legacy global version', () => {
    expect(trainingModelVersionLabel({ version_no: 17, branch_version_no: 3 })).toBe('v3');
    expect(trainingModelVersionLabel({ version_no: 17, branch_version_no: null })).toBe('v17');
    expect(trainingModelVersionLabel(null)).toBe('版本未记录');
    expect(trainingIterationParentLabel({
      job_id: 9,
      task_id: 7,
      display_name: '白天下雨模型',
      version_no: 17,
      branch_version_no: 3,
      weights_filename: 'best.pt',
    })).toBe('白天下雨模型 · v3 · best.pt');
  });

  it('changes the iteration snapshot key when a selected candidate contract or readiness changes', () => {
    const candidate = {
      task_id: 7,
      task_name: '迭代任务',
      dataset_id: 1,
      dataset_label_contract_id: 3,
      label_set_signature: 'labels-a',
      class_order_signature: 'order-a',
      training_ready: true,
      trainable_sample_count: 20,
      contract_labels: [{ label_id: 11, label_value: 'person' }],
    };
    const changedContract = { ...candidate, label_set_signature: 'labels-b' };
    const changedReadiness = { ...candidate, trainable_sample_count: 0, training_ready: false };

    expect(trainingCandidateSnapshotKey(candidate)).not.toBe(trainingCandidateSnapshotKey(changedContract));
    expect(trainingCandidateSnapshotKey(candidate)).not.toBe(trainingCandidateSnapshotKey(changedReadiness));
    expect(trainingCandidateSelectionSnapshotKey([candidate], [7])).not.toBe(
      trainingCandidateSelectionSnapshotKey([changedContract], [7]),
    );
    expect(trainingCandidateSelectionSnapshotKey([candidate], [7])).toContain('7:');
  });

  it('requires both label-set and class-order signatures for task merges', () => {
    const candidate = {
      task_id: 7,
      dataset_label_contract_id: 3,
      label_set_signature: 'labels-a',
      class_order_signature: 'order-a',
      contract_labels: [
        { label_id: 11, label_value: 'person' },
        { label_id: 12, label_value: 'vehicle' },
      ],
    };
    const sameContractDifferentApiOrder = {
      ...candidate,
      task_id: 8,
      contract_labels: [...candidate.contract_labels].reverse(),
    };
    const differentLabelSet = { ...candidate, task_id: 9, label_set_signature: 'labels-b' };
    const differentClassOrder = { ...candidate, task_id: 10, class_order_signature: 'order-b' };

    expect(trainingCandidateContractKey(candidate)).toBe('signatures:labels-a:order-a');
    expect(trainingCandidatesShareLabelContract(candidate, sameContractDifferentApiOrder)).toBe(true);
    expect(trainingCandidatesShareLabelContract(candidate, differentLabelSet)).toBe(false);
    expect(trainingCandidatesShareLabelContract(candidate, differentClassOrder)).toBe(false);
  });

  it('disables manually selected parent models with missing lineage signatures', () => {
    expect(trainingModelVersionCompatibility(
      { id: 9, version_no: 2, display_name: '历史模型' },
      'labels-a',
      'order-a',
    )).toEqual({ compatible: false, disabled: true, reason: 'contract_missing' });
    expect(trainingModelVersionCompatibility(
      { id: 10, version_no: 1, display_name: '错误类别顺序', label_set_signature: 'labels-a', class_order_signature: 'order-b' },
      'labels-a',
      'order-a',
    )).toEqual({ compatible: false, disabled: true, reason: 'contract_mismatch' });
    expect(trainingModelVersionCompatibility(
      { id: 11, version_no: 1, display_name: '兼容模型', label_set_signature: 'labels-a', class_order_signature: 'order-a' },
      'labels-a',
      'order-a',
    )).toEqual({ compatible: true, disabled: false });
  });

  it('pins the model shown by the iteration preview into the create payload', () => {
    expect(pinIterationWeights('auto', 'yolo11n.pt', undefined, {
      weights_source: 'trained', model_weights: 'trained:42', weights_ref_id: 42,
      parent: { job_id: 42, task_id: 7, display_name: '道路检测', weights_filename: 'best.pt' }, suggested_params: {},
    })).toEqual({ weights_source: 'trained', weights_ref_id: 42, model_weights: 'trained:42' });
    expect(pinIterationWeights('auto', 'yolo11n.pt', undefined, {
      weights_source: 'preset', model_weights: 'yolo11n.pt', fallback_reason: 'no_compatible_completed_parent', suggested_params: {},
    })).toEqual({ weights_source: 'preset', weights_ref_id: undefined, model_weights: 'yolo11n.pt' });
  });

  it('does not carry an incomplete trained or imported reference into the create payload', () => {
    expect(pinIterationWeights('trained', 'trained:42', undefined)).toEqual({
      weights_source: 'preset', weights_ref_id: undefined, model_weights: 'yolo11n.pt',
    });
    expect(pinIterationWeights('imported', 'imported:7', undefined)).toEqual({
      weights_source: 'preset', weights_ref_id: undefined, model_weights: 'yolo11n.pt',
    });
  });

  it('requires a registered remote node when remote execution is selected', () => {
    expect(isRemoteNodeMissing('remote')).toBe(true);
    expect(isRemoteNodeMissing('remote', '  ')).toBe(true);
    expect(isRemoteNodeMissing('remote', 'gpu-node-01')).toBe(false);
    expect(isRemoteNodeMissing('local')).toBe(false);
  });

  it('keeps generic algorithms on the container worker contract', () => {
    expect(isUnsupportedGenericExecution('classification', 'local')).toBe(true);
    expect(isUnsupportedGenericExecution('classification', 'remote')).toBe(true);
    expect(isUnsupportedGenericExecution('classification', 'container')).toBe(false);
    expect(isUnsupportedGenericExecution('yolo_detection', 'remote')).toBe(false);
  });

  it('limits legacy YOLO creation to remote or task-only compatibility cases', () => {
    expect(shouldUseLegacyYoloCreate('yolo_detection', 'remote', 12)).toBe(true);
    expect(shouldUseLegacyYoloCreate('yolo_detection', 'local')).toBe(true);
    expect(shouldUseLegacyYoloCreate('yolo_detection', 'local', 12)).toBe(false);
    expect(shouldUseLegacyYoloCreate('classification', 'container', 12)).toBe(false);
  });

  it('preserves hidden wizard values when confirmation-step validation omits them', () => {
    expect(mergeTrainingFormValues(
      { dataset_version_id: 1, algorithm_key: 'yolo_detection', execution_mode: 'local' },
      { model_weights: 'yolo11n.pt', epochs: 50, imgsz: 640 },
    )).toMatchObject({
      dataset_version_id: 1,
      algorithm_key: 'yolo_detection',
      execution_mode: 'local',
      model_weights: 'yolo11n.pt',
    });
  });

  it('parses a versioned recipe reference without leaking malformed values', () => {
    expect(parseTrainingRecipeRef('yolov11_detection::2')).toEqual({ training_recipe_key: 'yolov11_detection', training_recipe_version: 2 });
    expect(parseTrainingRecipeRef('  classification::1 ')).toEqual({ training_recipe_key: 'classification', training_recipe_version: 1 });
    expect(parseTrainingRecipeRef('broken')).toEqual({ training_recipe_key: 'broken', training_recipe_version: undefined });
    expect(parseTrainingRecipeRef('')).toEqual({ training_recipe_key: undefined, training_recipe_version: undefined });
  });

  it('resolves the一期默认训练方案 from the catalog without exposing duplicate choices', () => {
    expect(resolveDefaultTrainingContract([
      { algorithm_key: 'classification', name: '通用图像分类', default_recipe: 'classification_v1' },
      { algorithm_key: 'yolo_detection', name: 'YOLO 通用目标检测', default_recipe: 'yolo_detection_v1', status: 'ACTIVE' },
    ], [
      { recipe_key: 'yolo_detection_v1', version: 1, name: 'YOLO 通用检测', job_type: 'yolo_detection', status: 'active' },
    ])).toEqual({
      algorithmKey: 'yolo_detection',
      recipeRef: 'yolo_detection_v1::1',
      algorithmName: 'YOLO 通用目标检测',
      recipeName: 'YOLO 通用检测',
    });
    expect(resolveDefaultTrainingContract()).toMatchObject({
      algorithmKey: 'yolo_detection',
      recipeRef: 'yolo_detection_v1::1',
    });
  });

  it('binds the plate detection target to the pose recipe and preset contract', () => {
    const recipes = [
      {
        recipe_key: 'plate_detection_v1',
        version: 1,
        job_type: 'plate_detection',
        model_family: 'plate_detection',
        status: 'active',
      },
      {
        recipe_key: 'plate_recognition_v1',
        version: 1,
        job_type: 'plate_recognition',
        model_family: 'plate_recognition',
        status: 'active',
      },
    ];

    expect(isPlateTrainingTarget('plate_detection')).toBe(true);
    expect(isPlateTrainingTarget('generic_detection')).toBe(false);
    expect(trainingTargetFromRecipe(recipes[0])).toBe('plate_detection');
    expect(resolveTrainingTargetContract('plate_detection', recipes)).toMatchObject({
      recipeRef: 'plate_detection_v1::1',
      trainingRecipeKey: 'plate_detection_v1',
      trainingRecipeVersion: 1,
      rectPresetSlug: 'plateDetection',
      plateTrainingTarget: 'plate_detection',
      modelWeights: 'yolov8n-pose.pt',
    });
    expect(trainingTargetOptions(recipes).map((item) => item.value)).toEqual([
      'generic_detection', 'plate_detection',
    ]);
    expect(trainingTargetOptions(recipes).some((item) => item.value === 'plate_recognition')).toBe(false);
  });

  it('normalizes legacy recognition deep links to the detection-only target', () => {
    window.history.replaceState({}, '', '/training?task_id=42&training_target=plate_recognition');

    expect(trainingEntryParamsFromLocation()).toEqual({
      taskId: 42,
      target: 'plate_detection',
    });
  });

  it('shows real epoch progress without estimating from elapsed time', () => {
    expect(trainingProgressSummary({ epochs: 50, current_epoch: 17 })).toEqual({
      currentEpoch: 17,
      targetEpochs: 50,
      percent: 34,
      label: '17 / 50 轮',
    });
    expect(trainingProgressSummary({ epochs: 50, status: 'PENDING' })).toMatchObject({
      currentEpoch: null,
      targetEpochs: 50,
      percent: null,
      label: '尚未开始 / 50 轮',
    });
    expect(trainingProgressSummary({ epochs: 50, actual_epochs: 50, status: 'COMPLETED' }).percent).toBe(100);
    expect(trainingProgressSummary({ epochs: 50, status: 'COMPLETED' })).toMatchObject({
      currentEpoch: 50,
      percent: 100,
      label: '50 / 50 轮',
    });
    expect(trainingProgressSummary({ epochs: 50, params: { current_epoch: 8 } }).label).toBe('8 / 50 轮');
  });

  it('builds a server-side two-dimensional candidate query', () => {
    expect(trainingCandidateQueryParams([15, 12], 3)).toEqual({
      page: 0,
      size: 100,
      label_ids: [15, 12],
      dataset_id: 3,
    });
    expect(trainingCandidateQueryParams()).toEqual({
      page: 0,
      size: 100,
    });
    expect(trainingCandidateQueryParamsForDimension('task', [15, 12], 3)).toEqual({
      page: 0,
      size: 100,
      dataset_id: 3,
    });
    expect(trainingCandidateQueryParamsForDimension('label', [15, 12], 3)).toEqual({
      page: 0,
      size: 100,
      label_ids: [15, 12],
      dataset_id: 3,
    });
    expect(trainingCandidateQueryParamsForDimension('task', [], 3, 0, 100, undefined, [], 'plate_detection_v1')).toEqual({
      page: 0,
      size: 100,
      dataset_id: 3,
      training_recipe_key: 'plate_detection_v1',
    });
    expect(trainingCandidateQueryParams([15], 3, 2, 20, '  道路  ')).toEqual({
      page: 2,
      size: 20,
      label_ids: [15],
      dataset_id: 3,
      q: '道路',
    });
    expect(trainingCandidateQueryParams([], 7, 0, 20, undefined, [7, 9, 7])).toEqual({
      page: 0,
      size: 20,
      dataset_ids: [7, 9],
    });
  });

  it('routes task selections spanning tasks or datasets through the merge endpoint', () => {
    expect(taskTrainingUsesMergedEndpoint([21], [7])).toBe(false);
    expect(taskTrainingUsesMergedEndpoint([21, 22], [7])).toBe(true);
    expect(taskTrainingUsesMergedEndpoint([21], [7, 9])).toBe(true);
    expect(taskTrainingUsesMergedEndpoint([21], [7, 7, 9])).toBe(true);
    expect(taskTrainingUsesMergedEndpoint([], [7, 9])).toBe(true);
    // The old guard is retained only for extension compatibility.
    expect(taskTrainingDatasetSelectionError([21], [7, 9])).toBeNull();
  });

  it('preserves candidate pagination metadata from both response envelopes', () => {
    expect(trainingCandidateBrowserResult({
      data: [{ task_id: 21, task_name: '第 2 页任务' }],
      meta_data: { total: 21, page: 1, size: 20 },
    })).toEqual({
      items: [{ task_id: 21, task_name: '第 2 页任务' }],
      total: 21,
      page: 1,
      size: 20,
    });
    expect(trainingCandidateBrowserResult({
      data: {
        data: [{ task_id: 1 }],
        meta_data: { total: 1, page: 0, size: 1 },
      },
    })).toMatchObject({ total: 1, page: 0, size: 1 });
  });

  it('prefers the backend reason for training-create conflicts', () => {
    expect(trainingCreateErrorMessage({
      response: { status: 409, data: { msg: '相同来源和样本已经生成过数据版本', err_code: 'CLIENT_ERROR' } },
    })).toBe('相同来源和样本已经生成过数据版本');
    expect(trainingCreateErrorMessage({ response: { status: 409, data: { message: '资源已变化' } } })).toBe('资源已变化');
    expect(trainingCreateErrorMessage({ response: { status: 409, data: {} } })).toBe('任务或训练资源状态已变化，请刷新候选任务后重试');
  });

  it('limits task-label choices to labels present in the selected dataset candidates', () => {
    expect(trainingLabelIdsForCandidates([
      { task_id: 1, contract_labels: [{ label_id: 3 }, { label_id: 1 }] },
      { task_id: 2, contract_labels: [{ label_id: 3 }, { label_id: 2 }] },
      { task_id: 3, contract_labels: [] },
    ])).toEqual([1, 2, 3]);
  });

  it('keeps task completion separate from the number of samples admitted to training', () => {
    const partial = {
      task_id: 1003,
      task_status: 'INPROGRESS',
      training_state: 'PARTIAL_READY',
      total_sample_count: 2000,
      completed_sample_count: 1628,
      trainable_sample_count: 1540,
      excluded_sample_count: 88,
      completion_ratio: 0.814,
      quality_pass_ratio: 0.946,
    };
    expect(trainingCandidateStats(partial)).toMatchObject({
      total: 2000,
      completed: 1628,
      trainable: 1540,
      pending: 372,
      excluded: 88,
    });
    expect(trainingCandidateState(partial)).toBe('PARTIAL_READY');
    expect(trainingCandidateStateLabel(trainingCandidateState(partial))).toBe('部分可训练');
    expect(trainingCandidateCanTrain(partial)).toBe(true);
    expect(trainingCandidateRatioLabel(0.814)).toBe('81.4%');
  });

  it('marks a complete task as ready while preserving the frozen training count', () => {
    const complete = {
      task_id: 1004,
      training_ready: true,
      total_sample_count: 1000,
      done_sample_count: 1000,
      trainable_sample_count: 1000,
    };
    expect(trainingCandidateState(complete)).toBe('COMPLETE_READY');
    expect(trainingCandidateStats(complete)).toMatchObject({
      total: 1000,
      completed: 1000,
      trainable: 1000,
      pending: 0,
      excluded: 0,
    });
  });

  it('does not allow a candidate below the configured training threshold', () => {
    const blocked = {
      task_id: 1005,
      training_state: 'PARTIAL_READY',
      trainable_sample_count: 8,
      minimum_threshold_met: false,
      minimum_threshold_reason: '当前可训练图片不足 20 张',
    };
    expect(trainingCandidateState(blocked)).toBe('NOT_READY');
    expect(trainingCandidateCanTrain(blocked)).toBe(false);
  });

  it('maps deep-link views to the legacy panels and defaults to the dataset workbench', () => {
    const original = window.location.href;
    window.history.replaceState({}, '', '/training?view=auto-plan');
    expect(trainingViewFromLocation()).toBe('auto-plan');
    window.history.replaceState({}, '', '/training?view=reports');
    expect(trainingViewFromLocation()).toBe('reports');
    window.history.replaceState({}, '', '/training?view=unknown');
    expect(trainingViewFromLocation()).toBe('datasets');
    window.history.replaceState({}, '', '/training?view=jobs');
    expect(trainingViewFromLocation()).toBe('jobs');
    window.history.replaceState({}, '', original);
  });

  it('maps all automatic-plan lifecycle states to user-facing Chinese labels', () => {
    expect(autoTrainingPlanStatusLabel('ACTIVE')).toBe('运行中');
    expect(autoTrainingPlanStatusLabel('AUTO_QUALITY_REACHED')).toBe('训练指标达标，待发布');
    expect(autoTrainingPlanStatusLabel('WAITING_FOR_REVIEW')).toBe('等待审核');
    expect(autoTrainingPlanStatusLabel('unknown_state')).toBe('unknown_state');
    expect(autoTrainingPlanStatusLabel(undefined)).toBe('未确认');
  });

  it('builds compatible job and evaluation deep links with task and job scope', () => {
    expect(legacyTrainingJobUrl(7, 42)).toBe('/yolo-training?embedded=1&panel=list&taskId=7&jobId=42');
    expect(evaluationHubUrl(7, 42)).toBe('/yolo-eval-reports-hub?taskId=7&jobId=42');
    expect(evaluationHubUrl(undefined, 42)).toBe('/yolo-eval-reports-hub');
  });

  it('requires a logical dataset before loading training candidates', async () => {
    mockedGet.mockResolvedValue({ data: { data: [] } } as never);
    renderPage();
    fireEvent.click(screen.getByRole('button', { name: /新建训练作业/ }));
    expect(await screen.findByText('请选择逻辑数据集后加载候选任务')).toBeTruthy();
    expect(screen.queryByText(/任务标签/)).toBeNull();
    expect(screen.getByText('这是第一层筛选条件，可避免一次性加载全部已标注任务。')).toBeTruthy();
    expect((screen.getByRole('button', { name: /下一步/ }) as HTMLButtonElement).disabled).toBe(true);
    await waitFor(() => expect(mockedGet).toHaveBeenCalledWith('/v1/training/catalog'));
    expect(mockedGet.mock.calls.flatMap((call) => call.slice(1))).not.toContainEqual(
      expect.objectContaining({ params: expect.objectContaining({ project_id: expect.anything() }) }),
    );
  });

  it('allows compatible task candidates to be merged and submits the task list', async () => {
    mockedGet.mockImplementation(async (path: string) => {
      if (path === '/v1/training/catalog') return { data: { data: {
        datasets: [{ id: 1, name: '人体识别逻辑数据集' }],
        algorithms: [], training_recipes: [], dataset_versions: [], pending_dataset_versions: [],
      } } } as never;
      if (path === '/v1/training/eligible-tasks') return { data: { data: [
        {
          task_id: 1, task_name: '人体识别任务一', training_ready: true,
          total_sample_count: 20, done_sample_count: 20, trainable_sample_count: 20,
          dataset_label_contract_id: 7, label_set_signature: 'labels-a', class_order_signature: 'order-a',
        },
        {
          task_id: 2, task_name: '人体识别任务二', training_ready: true,
          total_sample_count: 30, done_sample_count: 30, trainable_sample_count: 30,
          dataset_label_contract_id: 7, label_set_signature: 'labels-a', class_order_signature: 'order-a',
        },
        {
          task_id: 4, task_name: '类别顺序不同任务', training_ready: true,
          total_sample_count: 35, done_sample_count: 35, trainable_sample_count: 35,
          dataset_label_contract_id: 7, label_set_signature: 'labels-a', class_order_signature: 'order-b',
        },
        {
          task_id: 3, task_name: '不兼容标签契约任务', training_ready: true,
          total_sample_count: 40, done_sample_count: 40, trainable_sample_count: 40,
          dataset_label_contract_id: 8,
        },
      ] } } as never;
      if (path === '/v1/training/iteration-preview') return { data: { data: {
        weights_source: 'preset', model_weights: 'yolo11n.pt', suggested_params: {},
      } } } as never;
      return { data: { data: { labels: [], totals: {} } } } as never;
    });
    mockedPost.mockResolvedValue({ data: { data: {} } } as never);

    renderPage();
    fireEvent.click(screen.getByRole('button', { name: /新建训练作业/ }));
    fireEvent.mouseDown((await screen.findAllByRole('combobox'))[0]);
    fireEvent.click(await screen.findByText('人体识别逻辑数据集'));
    expect(await screen.findByText('选择一个或多个标注任务，系统使用所选任务中已完成且质量合格的图片训练。')).toBeTruthy();
    await waitFor(() => expect(mockedGet.mock.calls.some(([path, config]) => (
      path === '/v1/training/eligible-tasks'
      && config?.params?.dataset_id === 1
      && config?.params?.training_dimension === 'task'
    ))).toBe(true));

    const first = await screen.findByRole('checkbox', { name: /人体识别任务一/ });
    const second = await screen.findByRole('checkbox', { name: /人体识别任务二/ });
    const differentOrder = await screen.findByRole('checkbox', { name: /类别顺序不同任务/ });
    const incompatible = await screen.findByRole('checkbox', { name: /不兼容标签契约任务/ });
    fireEvent.click(first);
    expect((differentOrder as HTMLInputElement).disabled).toBe(true);
    expect((incompatible as HTMLInputElement).disabled).toBe(true);
    fireEvent.click(second);
    expect(await screen.findByText('已选 2 个标注任务，将合并约 50 张可训练图片。')).toBeTruthy();
    expect((screen.getByRole('button', { name: /下一步/ }) as HTMLButtonElement).disabled).toBe(false);

    fireEvent.click(screen.getByRole('button', { name: /下一步/ }));
    fireEvent.change(screen.getByLabelText('训练任务名称'), { target: { value: '人体识别合并训练' } });
    await waitFor(() => expect(screen.queryByText('正在确认本次迭代模型和 AI 参数')).toBeNull());
    fireEvent.click(screen.getByRole('button', { name: /下一步/ }));
    fireEvent.click(await screen.findByRole('button', { name: /开始训练/ }));

    await waitFor(() => expect(mockedPost).toHaveBeenCalledWith(
      '/v1/yolo-training-tasks',
      expect.objectContaining({
        name: '人体识别合并训练',
        selected_task_ids: [1, 2],
        dataset_id: 1,
        training_recipe_key: 'yolov11_detection',
        training_recipe_version: 1,
        hyperparams: expect.objectContaining({ source_task_ids: [1, 2], execution_mode: 'local' }),
      }),
      expect.anything(),
    ));
    const mergePayload = mockedPost.mock.calls[0]?.[1] as Record<string, unknown>;
    expect(mergePayload).not.toHaveProperty('label_ids');
  }, 15000);

  it('includes selected global tool-tag conditions in the training payload and confirmation', async () => {
    mockedGet.mockImplementation(async (path: string) => {
      if (path === '/v1/training/catalog') return { data: { data: {
        datasets: [{ id: 1, name: '全局条件数据集' }],
        algorithms: [], training_recipes: [], dataset_versions: [], pending_dataset_versions: [],
      } } } as never;
      if (path === '/v1/annotation-taxonomy/global-tag-dimensions') return { data: [{
        id: 1, field_key: 'weather', name_cn: '天气环境', options: [{ id: 2, name_cn: '雨天', value_en: 'rain' }],
      }], meta_data: { total: 1, page: 0, size: 1 } } as never;
      if (path === '/v1/training/eligible-tasks') return { data: { data: [{
        task_id: 1, task_name: '全局条件任务', training_ready: true,
        total_sample_count: 12, done_sample_count: 12, trainable_sample_count: 12,
        label_set_signature: 'labels-a', class_order_signature: 'order-a',
      }] } } as never;
      if (path === '/v1/training/iteration-preview') return { data: { data: {
        weights_source: 'preset', model_weights: 'yolo11n.pt', suggested_params: {},
      } } } as never;
      return { data: { data: { labels: [], totals: {} } } } as never;
    });
    mockedPost.mockResolvedValue({ data: { data: {} } } as never);

    renderPage();
    fireEvent.click(screen.getByRole('button', { name: /新建训练作业/ }));
    fireEvent.mouseDown((await screen.findAllByRole('combobox'))[0]);
    fireEvent.click(await screen.findByText('全局条件数据集'));
    fireEvent.click(await screen.findByRole('checkbox', { name: /全局条件任务/ }));

    expect(await screen.findByText('全局工具标签筛选（可选）')).toBeTruthy();
    const conditionSelect = (await screen.findAllByRole('combobox')).at(-1);
    expect(conditionSelect).toBeTruthy();
    fireEvent.mouseDown(conditionSelect!);
    fireEvent.click(await screen.findByText('雨天（rain）'));
    expect(await screen.findByText('已选 1 个维度')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: /下一步/ }));
    await waitFor(() => expect(screen.queryByText('正在确认本次迭代模型和 AI 参数')).toBeNull());
    await waitFor(() => expect(mockedGet.mock.calls.some(([path, config]) => (
      path === '/v1/training/iteration-preview'
      && config?.params?.sample_conditions === trainingSampleConditionsJson({ weather: ['rain'] })
    ))).toBe(true));
    fireEvent.click(screen.getByRole('button', { name: /下一步/ }));
    expect(await screen.findByText('天气环境：雨天（rain）')).toBeTruthy();
    fireEvent.click(await screen.findByRole('button', { name: /开始训练/ }));

    await waitFor(() => expect(mockedPost).toHaveBeenCalledWith('/v1/training/jobs', expect.objectContaining({
      sample_conditions: { weather: ['rain'] },
    }), expect.anything()));
  }, 15000);

  it('shows the real same-series parent and submits its AI iteration params', async () => {
    mockedGet.mockImplementation(async (path: string) => {
      if (path === '/v1/training/catalog') return { data: { data: {
        datasets: [{ id: 1, name: '迭代测试数据集' }],
        algorithms: [], training_recipes: [], dataset_versions: [], pending_dataset_versions: [],
      } } } as never;
      if (path === '/v1/training/eligible-tasks') return { data: { data: [{
        task_id: 7, task_name: '迭代任务', training_ready: true,
        total_sample_count: 30, done_sample_count: 30, trainable_sample_count: 30,
      }] } } as never;
      if (path === '/v1/training/iteration-preview') return { data: { data: {
        weights_source: 'trained', weights_ref_id: 2, model_weights: 'trained:2',
        parent: { job_id: 2, task_id: 7, display_name: '人体识别', version_no: 3, dataset_id: 1, dataset_name: '父模型数据集', dataset_version_no: 4, dataset_content_signature: 'dataset-content-abc', weights_filename: 'best.pt' },
        ai_report_id: 91, ai_provider_key: 'volcengine',
        suggested_params: { epochs: 20, imgsz: 768, lr0: 0.0001, amp: false },
      } } } as never;
      return { data: { data: { labels: [], totals: {} } } } as never;
    });
    mockedPost.mockResolvedValue({ data: { data: {} } } as never);
    renderPage();
    fireEvent.click(screen.getByRole('button', { name: /新建训练作业/ }));
    const datasetSelect = (await screen.findAllByRole('combobox'))[0];
    fireEvent.mouseDown(datasetSelect);
    fireEvent.click(await screen.findByText('迭代测试数据集'));
    fireEvent.click(await screen.findByRole('checkbox', { name: /迭代任务/ }));
    fireEvent.click(screen.getByRole('button', { name: /下一步/ }));

    expect(await screen.findByText(/将继承：人体识别 · v3 · best.pt/)).toBeTruthy();
    expect(await screen.findByText(/父模型数据集（#1）/)).toBeTruthy();
    expect(await screen.findByText('dataset-content-abc')).toBeTruthy();
    expect(await screen.findByText('已应用 AI 报告 #91 的下一轮参数')).toBeTruthy();
    expect(screen.getByDisplayValue('20')).toBeTruthy();
    expect(screen.getByDisplayValue('768')).toBeTruthy();
    expect(screen.getByText('0.0001')).toBeTruthy();
    expect(screen.queryByText('权重引用 ID')).toBeNull();
    expect(screen.queryByText('初始权重名称')).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: /下一步/ }));
    expect(await screen.findByText(/已应用 AI 报告 #91，共 4 项参数/)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: /开始训练/ }));
    await waitFor(() => expect(mockedPost).toHaveBeenCalledWith('/v1/training/jobs', expect.objectContaining({
      weights_source: 'trained', weights_ref_id: 2, model_weights: 'trained:2', epochs: 20, imgsz: 768,
      params: { lr0: 0.0001, amp: false },
    }), expect.anything()));
  });

  it('selects a readable trained model version instead of exposing reference IDs', async () => {
    mockedGet.mockImplementation(async (path: string) => {
      if (path === '/v1/training/catalog') return { data: { data: {
        datasets: [{ id: 1, name: '迭代测试数据集' }], algorithms: [], training_recipes: [], dataset_versions: [], pending_dataset_versions: [],
      } } } as never;
      if (path === '/v1/training/eligible-tasks') return { data: { data: [{
        task_id: 7, task_name: '迭代任务', training_ready: true, total_sample_count: 30, done_sample_count: 30, trainable_sample_count: 30,
      }] } } as never;
      if (path === '/v1/models/families') return { data: { data: [{
        family_key: 'task:99:algorithm:1', task_id: 99, display_name: '人体识别', versions: [{ id: 99, training_job_id: 2, version_no: 3, display_name: '人体识别 · v3' }],
      }] } } as never;
      if (path === '/v1/training/iteration-preview') return { data: { data: {
        weights_source: 'trained', weights_ref_id: 2, model_weights: 'trained:2',
        parent: { job_id: 2, task_id: 7, display_name: '人体识别', version_no: 3, weights_filename: 'best.pt' },
        suggested_params: {},
      } } } as never;
      return { data: { data: { labels: [], totals: {} } } } as never;
    });
    mockedPost.mockResolvedValue({ data: { data: {} } } as never);
    renderPage();
    fireEvent.click(screen.getByRole('button', { name: /新建训练作业/ }));
    fireEvent.mouseDown((await screen.findAllByRole('combobox'))[0]);
    fireEvent.click(await screen.findByText('迭代测试数据集'));
    fireEvent.click(await screen.findByRole('checkbox', { name: /迭代任务/ }));
    fireEvent.click(screen.getByRole('button', { name: /下一步/ }));
    fireEvent.mouseDown(await screen.findByRole('combobox', { name: '初始权重来源' }));
    fireEvent.click(await screen.findByText('指定本系统已训练模型'));
    fireEvent.mouseDown(await screen.findByRole('combobox', { name: '选择本系统已训练模型' }));
    fireEvent.click(await screen.findByText(/人体识别 · v3 · 来源标注任务 #99 · 待预检确认/));

    expect(await screen.findByText(/本次迭代模型：人体识别 · v3 · best.pt/)).toBeTruthy();
    expect(screen.getAllByText(/来源标注任务 #99/).length).toBeGreaterThan(0);
    const familyRequest = mockedGet.mock.calls.find(([path]) => path === '/v1/models/families');
    expect(familyRequest?.[1]?.params?.task_id).toBeUndefined();
    expect(screen.queryByText('权重引用 ID')).toBeNull();
    expect(screen.queryByText('初始权重名称')).toBeNull();
    expect(screen.getByText('该模型暂无 AI 下轮参数')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: /下一步/ }));
    fireEvent.click(await screen.findByRole('button', { name: /开始训练/ }));
    await waitFor(() => expect(mockedPost).toHaveBeenCalledWith('/v1/training/jobs', expect.objectContaining({
      weights_source: 'trained', weights_ref_id: 2, model_weights: 'trained:2', params: {},
    }), expect.anything()));
  }, 15000);

  it('filters AI iteration values into top-level fields and real train hyperparams', () => {
    expect(partitionAiIterationParams({ epochs: 20, imgsz: 640, lr0: 0.0001, model_weights: 'bad.pt', device: 'cuda:1' })).toEqual({
      topLevel: { epochs: 20, imgsz: 640 }, hyperparams: { lr0: 0.0001 },
    });
    expect(trainingIterationParentLabel({ job_id: 2, task_id: 7, display_name: '人体识别', version_no: 3, weights_filename: 'best.pt' })).toBe('人体识别 · v3 · best.pt');
  });

  it('shows target labels only after switching to label-dimension training', async () => {
    mockedGet.mockResolvedValue({ data: { data: [] } } as never);
    renderPage();
    fireEvent.click(screen.getByRole('button', { name: /新建训练作业/ }));
    expect(await screen.findByText('请选择逻辑数据集后加载候选任务')).toBeTruthy();
    expect(screen.queryByText('目标标签', { exact: true })).toBeNull();

    fireEvent.click(screen.getByRole('radio', { name: /按标签训练/ }));

    expect(await screen.findByText('目标标签', { exact: true })).toBeTruthy();
    expect(screen.queryByText(/任务标签/)).toBeNull();
  });

  it('searches target labels on the server and keeps selections across pages', async () => {
    const labelRequests: any[] = [];
    mockedGet.mockImplementation(async (path: string, config?: any) => {
      if (path === '/v1/training/catalog') {
        return { data: { data: {
          datasets: [{ id: 1, name: '标签数据集' }],
          algorithms: [], training_recipes: [], dataset_versions: [], pending_dataset_versions: [],
        } } } as never;
      }
      if (path === '/v1/annotation-taxonomy/labels') {
        labelRequests.push(config?.params || {});
        const page = Number(config?.params?.page || 0);
        const filtered = String(config?.params?.q || '').trim();
        const row = filtered
          ? { id: 2, label_key: 'vehicle', label_value: '车辆' }
          : page > 0
            ? { id: 2, label_key: 'person', label_value: '人员' }
            : { id: 1, label_key: 'car', label_value: '车辆' };
        return { data: { data: [row], meta_data: { total: 501, page, size: 1 } } } as never;
      }
      if (path === '/v1/training/eligible-tasks') {
        return { data: { data: [], meta_data: { total: 0, page: 0, size: 0 } } } as never;
      }
      return { data: { data: { labels: [], totals: {} } } } as never;
    });

    renderPage();
    fireEvent.click(screen.getByRole('button', { name: /新建训练作业/ }));
    fireEvent.click(screen.getByRole('radio', { name: /按标签训练/ }));
    const datasetSelect = (await screen.findAllByRole('combobox'))[0];
    fireEvent.mouseDown(datasetSelect);
    fireEvent.click(await screen.findByText('标签数据集'));

    const labelSearch = await screen.findByPlaceholderText('按标签名称、标识或描述搜索');
    await waitFor(() => expect(labelRequests.some((params) => (
      params.page === 0
      && params.size === TRAINING_LABEL_PAGE_SIZE
      && params.include_disabled === false
      && params.annotation_kind === 'bbox'
    ))).toBe(true));
    const labelSelect = (await screen.findAllByRole('combobox'))[1];
    fireEvent.mouseDown(labelSelect);
    fireEvent.click(await screen.findByText('车辆'));

    fireEvent.change(labelSearch, { target: { value: '人员' } });
    fireEvent.keyDown(labelSearch, { key: 'Enter', code: 'Enter' });
    await waitFor(() => expect(labelRequests.some((params) => params.q === '人员' && params.page === 0)).toBe(true));
    expect(screen.getAllByText('车辆').length).toBeGreaterThan(0);

    fireEvent.click(screen.getByTitle('2'));
    await waitFor(() => expect(labelRequests.some((params) => params.page === 1 && params.q === '人员')).toBe(true));
    expect(screen.getAllByText('车辆').length).toBeGreaterThan(0);

    fireEvent.change(labelSearch, { target: { value: '' } });
    await waitFor(() => expect(labelRequests.some((params) => params.page === 0 && !params.q)).toBe(true));
  });

  it('searches imported models on the server and keeps the selected model visible', async () => {
    const modelRequests: any[] = [];
    mockedGet.mockImplementation(async (path: string, config?: any) => {
      if (path === '/v1/training/catalog') {
        return { data: { data: {
          datasets: [{ id: 1, name: '外部模型数据集' }],
          algorithms: [], training_recipes: [], dataset_versions: [], pending_dataset_versions: [],
        } } } as never;
      }
      if (path === '/v1/training/eligible-tasks') {
        return { data: { data: [{
          task_id: 1, task_name: '外部模型任务', training_ready: true,
          total_sample_count: 20, done_sample_count: 20, trainable_sample_count: 20,
        }] } } as never;
      }
      if (path === '/v1/yolo-imported-models') {
        modelRequests.push(config?.params || {});
        const search = String(config?.params?.q || '').trim();
        const page = Number(config?.params?.page || 0);
        const row = search
          ? { id: 2, display_name: '夜间模型', original_filename: 'night.pt', category: '夜间' }
          : page > 0
            ? { id: 3, display_name: '第三模型', original_filename: 'third.pt' }
            : { id: 1, display_name: '白天模型', original_filename: 'day.pt', category: '白天' };
        return { data: { data: [row], meta_data: { total: 41, page, size: 20 } } } as never;
      }
      if (path === '/v1/training/iteration-preview') {
        return { data: { data: { weights_source: 'preset', model_weights: 'yolo11n.pt', suggested_params: {} } } } as never;
      }
      return { data: { data: { labels: [], totals: {} } } } as never;
    });

    renderPage();
    fireEvent.click(screen.getByRole('button', { name: /新建训练作业/ }));
    fireEvent.mouseDown((await screen.findAllByRole('combobox'))[0]);
    fireEvent.click(await screen.findByText('外部模型数据集'));
    fireEvent.click(await screen.findByRole('checkbox', { name: /外部模型任务/ }));
    fireEvent.click(screen.getByRole('button', { name: /下一步/ }));

    const sourceSelect = await screen.findByRole('combobox', { name: '初始权重来源' });
    fireEvent.mouseDown(sourceSelect);
    fireEvent.click(await screen.findByText('外部导入模型'));
    const search = await screen.findByPlaceholderText('按模型名称、文件名或分类搜索');
    await waitFor(() => expect(modelRequests.some((params) => params.page === 0 && params.size === TRAINING_IMPORTED_MODEL_PAGE_SIZE)).toBe(true));

    const modelSelect = screen.getByRole('combobox', { name: '选择外部导入模型' });
    fireEvent.mouseDown(modelSelect);
    fireEvent.click(await screen.findByText(/白天模型/));
    expect(screen.getAllByText('白天模型').length).toBeGreaterThan(0);

    fireEvent.change(search, { target: { value: '夜间' } });
    fireEvent.keyDown(search, { key: 'Enter', code: 'Enter' });
    await waitFor(() => expect(modelRequests.some((params) => params.q === '夜间' && params.page === 0)).toBe(true));
    expect(screen.getAllByText('白天模型').length).toBeGreaterThan(0);

    fireEvent.click(screen.getByTitle('2'));
    await waitFor(() => expect(modelRequests.some((params) => params.page === 1 && params.q === '夜间')).toBe(true));
    expect(screen.getAllByText('白天模型').length).toBeGreaterThan(0);
  });

  it('keeps the label-training next action enabled after paging away from selected sources', async () => {
    mockedGet.mockImplementation(async (path: string, config?: any) => {
      if (path === '/v1/training/catalog') {
        return { data: { data: {
          datasets: [{ id: 1, name: '人员数据集', dataset_labels: [{ id: 11, label_value: 'person' }] }],
          algorithms: [], training_recipes: [], dataset_versions: [], pending_dataset_versions: [],
        } } } as never;
      }
      if (path === '/v1/annotation-taxonomy/labels') {
        return { data: { data: [{ id: 11, label_value: 'person' }] } } as never;
      }
      if (path === '/v1/training/eligible-tasks') {
        const page = Number(config?.params?.page || 0);
        if (page > 0) {
          return { data: { data: [], meta_data: { total: 21, page, size: 0 } } } as never;
        }
        return { data: { data: [{
          task_id: 7, task_name: '人员来源任务', training_ready: true,
          total_sample_count: 30, done_sample_count: 30, trainable_sample_count: 30,
        }], meta_data: { total: 21, page: 0, size: 1 } } } as never;
      }
      return { data: { data: { labels: [], totals: {} } } } as never;
    });

    renderPage();
    fireEvent.click(screen.getByRole('button', { name: /新建训练作业/ }));
    fireEvent.click(screen.getByRole('radio', { name: /按标签训练/ }));
    const datasetSelect = (await screen.findAllByRole('combobox'))[0];
    fireEvent.mouseDown(datasetSelect);
    fireEvent.click(await screen.findByText('人员数据集'));

    const labelSelect = (await screen.findAllByRole('combobox'))[1];
    fireEvent.mouseDown(labelSelect);
    fireEvent.click(await screen.findByText('person'));
    const source = await screen.findByRole('checkbox', { name: /人员来源任务/ });
    fireEvent.click(source);
    expect((screen.getByRole('button', { name: /下一步/ }) as HTMLButtonElement).disabled).toBe(false);

    fireEvent.click(screen.getByTitle('2'));
    await waitFor(() => expect(mockedGet.mock.calls.some(([path, config]) => (
      path === '/v1/training/eligible-tasks' && config?.params?.page === 1
    ))).toBe(true));
    await waitFor(() => expect(screen.getByText(/当前页 0 个/)).toBeTruthy());
    await waitFor(() => expect(screen.queryByRole('checkbox', { name: /人员来源任务/ })).toBeNull());
    expect((screen.getByRole('button', { name: /下一步/ }) as HTMLButtonElement).disabled).toBe(false);
  });

  it('allows removing a selected source after it becomes stale', async () => {
    let candidateRequestCount = 0;
    mockedGet.mockImplementation(async (path: string, config?: any) => {
      if (path === '/v1/training/catalog') {
        return { data: { data: {
          datasets: [{ id: 1, name: '人员数据集', dataset_labels: [{ id: 11, label_value: 'person' }] }],
          algorithms: [], training_recipes: [], dataset_versions: [], pending_dataset_versions: [],
        } } } as never;
      }
      if (path === '/v1/annotation-taxonomy/labels') {
        return { data: { data: [{ id: 11, label_value: 'person' }] } } as never;
      }
      if (path === '/v1/training/eligible-tasks') {
        candidateRequestCount += 1;
        const stale = String(config?.params?.q || '').trim() === '刷新';
        return { data: { data: [{
          task_id: 7,
          task_name: '人员来源任务',
          training_ready: !stale,
          minimum_threshold_met: !stale,
          total_sample_count: stale ? 30 : 30,
          done_sample_count: stale ? 20 : 30,
          trainable_sample_count: stale ? 0 : 30,
        }], meta_data: { total: 1, page: 0, size: 1 } } } as never;
      }
      return { data: { data: { labels: [], totals: {} } } } as never;
    });

    renderPage();
    fireEvent.click(screen.getByRole('button', { name: /新建训练作业/ }));
    fireEvent.click(screen.getByRole('radio', { name: /按标签训练/ }));
    const datasetSelect = (await screen.findAllByRole('combobox'))[0];
    fireEvent.mouseDown(datasetSelect);
    fireEvent.click(await screen.findByText('人员数据集'));
    const labelSelect = (await screen.findAllByRole('combobox'))[1];
    fireEvent.mouseDown(labelSelect);
    fireEvent.click(await screen.findByText('person'));

    const source = await screen.findByRole('checkbox', { name: /人员来源任务/ });
    fireEvent.click(source);
    expect((screen.getByRole('button', { name: /下一步/ }) as HTMLButtonElement).disabled).toBe(false);

    const search = screen.getByPlaceholderText('按来源任务名称或数据集名称搜索');
    fireEvent.change(search, { target: { value: '刷新' } });
    fireEvent.keyDown(search, { key: 'Enter', code: 'Enter' });
    await waitFor(() => expect(candidateRequestCount).toBeGreaterThan(1));
    await waitFor(() => expect(screen.getByText('暂不可训练')).toBeTruthy());

    // The stale selected row remains actionable so it can be removed. The
    // wizard must not permit proceeding while it remains selected.
    const staleSource = screen.getByRole('checkbox', { name: /人员来源任务/ });
    expect((staleSource as HTMLInputElement).disabled).toBe(false);
    expect((screen.getByRole('button', { name: /下一步/ }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(staleSource);
    await waitFor(() => expect((screen.getByRole('button', { name: /下一步/ }) as HTMLButtonElement).disabled).toBe(true));
  });

  it('loads the unified job list without project scope', async () => {
    mockedGet.mockResolvedValue({ data: { data: [], meta_data: { total: 0 } } } as never);
    renderPage();

    expect(resourcePageState.props.needsProject).toBe(false);
    await resourcePageState.props.queryFn(undefined, 2, 20);
    expect(mockedGet).toHaveBeenCalledWith('/v1/training/jobs', { params: { page: 2, size: 20 } });
  });

  it('separates training completion from a blocked quality gate', () => {
    expect(trainingStatusLabel('COMPLETED')).toBe('训练流程完成');
    expect(trainingRuntimeStatusLabel('COMPLETED')).toBe('已完成');
    expect(trainingQualityGateSummary({
      status: 'COMPLETED',
      release_gate_assessment: { status_code: 'blocked', reason_summary: '边端验证未完成' },
    })).toEqual({ level: 'blocked', label: '质量门禁未通过，发布已阻断', reason: '边端验证未完成' });
    expect(trainingQualityGateSummary({
      status: 'COMPLETED',
      release_gate_assessment: { status_code: 'publishable' },
    }).level).toBe('pass');
  });

  it('keeps the training surface focused on jobs and auto iteration', async () => {
    mockedGet.mockResolvedValue({ data: { data: [], meta_data: { total: 0 } } } as never);
    renderPage();

    expect(screen.getByRole('tab', { name: '训练作业' })).toBeTruthy();
    expect(screen.getByRole('tab', { name: '自动迭代设置' })).toBeTruthy();
    expect(screen.queryByRole('tab', { name: '历史自动训练计划' })).toBeNull();
    expect(screen.queryByRole('tab', { name: '训练报告' })).toBeNull();
  });

  it('keeps legacy training report URLs readable for old bookmarks without navigation entries', async () => {
    window.history.replaceState({}, '', '/training?view=reports');
    mockedGet.mockImplementation(async (requestPath: string) => {
      if (requestPath === '/v1/training/summary') {
        return { data: { data: { auto_training: { plan_count: 0, plans: [], evaluation_reports: [] }, reports: { auto: [], legacy: [], legacy_total: 0 } } } } as never;
      }
      return { data: { data: [] } } as never;
    });
    renderPage();

    expect(screen.queryByRole('tab', { name: '训练报告' })).toBeNull();
    expect(await screen.findByText('训练报告')).toBeTruthy();
  });


  it('pauses a processing job through the unified endpoint', async () => {
    mockedPost.mockResolvedValue({ data: { status: 'CANCELLED' } } as never);
    renderPage();
    fireEvent.click(within(screen.getByTestId('row-actions')).getByRole('button', { name: /暂停/ }));
    fireEvent.click(screen.getByRole('button', { name: '确认暂停' }));
    await waitFor(() => expect(mockedPost).toHaveBeenCalledWith('/v1/training/jobs/42/pause'));
  });

  it('retries a failed job through the unified endpoint', async () => {
    mockedPost.mockResolvedValue({ data: { status: 'PENDING' } } as never);
    renderPage();
    fireEvent.click(within(screen.getByTestId('failed-row-actions')).getByRole('button', { name: '重试训练' }));
    await waitFor(() => expect(mockedPost).toHaveBeenCalledWith('/v1/training/jobs/43/restart'));
  });

  it('shows only AI report for completed jobs and continue for cancelled jobs', () => {
    renderPage();
    const completed = within(screen.getByTestId('completed-row-actions'));
    expect(completed.getByRole('button', { name: 'AI报告' })).toBeTruthy();
    expect(completed.queryByRole('button', { name: /暂停|继续|重试/ })).toBeNull();
    const cancelled = within(screen.getByTestId('cancelled-row-actions'));
    expect(cancelled.getByRole('button', { name: '继续训练' })).toBeTruthy();
    expect(cancelled.queryByRole('button', { name: /暂停|AI报告/ })).toBeNull();
  });

});
