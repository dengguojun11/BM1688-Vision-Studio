import {
  ApiOutlined,
  AppstoreOutlined,
  AuditOutlined,
  BranchesOutlined,
  CheckCircleOutlined,
  CloudUploadOutlined,
  DatabaseOutlined,
  ExperimentOutlined,
  FileSearchOutlined,
  SettingOutlined,
  TagsOutlined,
} from '@ant-design/icons';

export const PRODUCT_NAME = '7GAI 进化平台';

export type PlatformModule =
  | 'workspace'
  | 'datasets'
  | 'ingestion'
  | 'annotation-review'
  | 'tasks'
  | 'algorithms'
  | 'rules'
  | 'providers'
  | 'training'
  | 'models'
  | 'evolution'
  | 'admin'
  | 'system-logs';

export const PLATFORM_MODULES: ReadonlyArray<{
  key: PlatformModule;
  label: string;
  description: string;
  icon: typeof AppstoreOutlined;
}> = [
  { key: 'workspace', label: '工作台', description: '查看待办、运行状态和阻断问题', icon: AppstoreOutlined },
  { key: 'tasks', label: '任务与标注', description: '管理标注任务、样本和人工标注工作', icon: TagsOutlined },
  { key: 'training', label: '训练中心', description: '管理训练作业、评估结果和失败处理', icon: ExperimentOutlined },
  {
    key: 'models',
    label: '模型中心',
    description: '管理模型版本、BM1688 制品、真机验证和发布',
    icon: BranchesOutlined,
  },
  {
    key: 'evolution',
    label: '进化中心',
    description: '查看第三方问题样本的自动接收、核验和分流',
    icon: CheckCircleOutlined,
  },
  { key: 'admin', label: '系统管理', description: '管理成员、权限、标签与集成', icon: SettingOutlined },
  {
    key: 'system-logs',
    label: '系统日志',
    description: '查看用户操作日志和系统操作日志',
    icon: FileSearchOutlined,
  },
];

export interface PlatformNavigationChild {
  key: string;
  label: string;
  to: string;
  /** Only managers can see and open this destination. */
  managerOnly?: boolean;
}

export interface PlatformNavigationGroup {
  key: PlatformModule;
  label: string;
  icon: typeof AppstoreOutlined;
  to: string;
  /** Only managers can see this entire first-level branch. */
  managerOnly?: boolean;
  children: ReadonlyArray<PlatformNavigationChild>;
}

/**
 * Two-level information architecture for the shell navigation.
 *
 * The first level is the operator-facing module (work / data / training /
 * models / evolution / administration); the second level is the concrete
 * working surface.  Keeping the destinations as URLs (including the existing
 * `?view=` and `?tab=` contracts) means browser back/forward, deep links and
 * the page-local tabs all stay in sync.
 */
export const PLATFORM_NAVIGATION: ReadonlyArray<PlatformNavigationGroup> = [
  {
    key: 'workspace',
    label: '工作台',
    icon: AppstoreOutlined,
    to: '/workspace/workbench',
    children: [
      { key: 'workspace-workbench', label: '运行工作台', to: '/workspace/workbench' },
      { key: 'workspace-screen', label: '数据大屏', to: '/workspace' },
      { key: 'workspace-evolution-screen', label: '模型进化大屏', to: '/workspace/evolution-screen' },
    ],
  },
  {
    key: 'tasks',
    label: '任务与标注',
    icon: TagsOutlined,
    to: '/tasks',
    children: [
      { key: 'tasks-list', label: '任务列表', to: '/tasks' },
    ],
  },
  {
    key: 'training',
    label: '训练中心',
    icon: ExperimentOutlined,
    to: '/training',
    children: [
      { key: 'training-jobs', label: '训练作业', to: '/training' },
      { key: 'training-iteration', label: '自动迭代设置', to: '/training?view=iteration-settings' },
    ],
  },
  {
    key: 'models',
    label: '模型中心',
    icon: BranchesOutlined,
    to: '/models',
    children: [
      { key: 'models-pt', label: 'PT 模型', to: '/models' },
      { key: 'models-compile', label: 'BM1688 编译', to: '/models?view=compile' },
      { key: 'models-bmodel', label: 'BModel 模型', to: '/models?view=bmodel' },
      { key: 'models-published', label: '已发布模型', to: '/models?view=published' },
      { key: 'models-external', label: '外部模型', to: '/models?view=external' },
      { key: 'models-quality-gates', label: '质量门禁', to: '/models?view=quality-gates' },
      { key: 'models-edge-tasks', label: '边端任务中心', to: '/edge-task-center' },
    ],
  },
  {
    key: 'evolution',
    label: '进化中心',
    icon: CheckCircleOutlined,
    to: '/evolution',
    children: [
      { key: 'evolution-requests', label: '模型优化请求', to: '/evolution' },
      { key: 'evolution-imports', label: '专家回流任务', to: '/evolution?tab=third-party' },
    ],
  },
  {
    key: 'admin',
    label: '系统管理',
    icon: SettingOutlined,
    to: '/admin',
    children: [
      { key: 'admin-accounts', label: '账号与角色', to: '/admin' },
      { key: 'admin-labels', label: '标签库', to: '/admin?view=governance&tab=labels' },
      { key: 'admin-governance-datasets', label: '数据集治理', to: '/admin?view=governance&tab=datasets' },
      { key: 'admin-global-tools', label: '全局工具标签', to: '/admin?view=governance&tab=global-tools' },
      { key: 'admin-datasets', label: '数据集管理', to: '/datasets', managerOnly: true },
      { key: 'admin-ingestion', label: '高级数据导入', to: '/ingestion', managerOnly: true },
      { key: 'admin-edge-devices', label: '边端设备管理', to: '/admin?view=edge-devices' },
      { key: 'admin-publish-config', label: '发布配置', to: '/admin?view=publish-config' },
      { key: 'admin-expert-s3', label: '专家 S3 回流配置', to: '/admin?view=expert-s3-config' },
      { key: 'admin-model-access', label: '模型接入配置', to: '/admin?view=model-access-config' },
      { key: 'admin-image-cleaning', label: '图片清洗配置', to: '/admin?view=image-cleaning' },
      { key: 'admin-annotation-settings', label: '标签与标注设置', to: '/annotation-settings' },
    ],
  },
  {
    key: 'system-logs',
    label: '系统日志',
    icon: FileSearchOutlined,
    to: '/system-logs?category=user',
    managerOnly: true,
    children: [
      { key: 'system-logs-user', label: '用户操作日志', to: '/system-logs?category=user' },
      { key: 'system-logs-system', label: '系统操作日志', to: '/system-logs?category=system' },
    ],
  },
];

/** Resolve the active first/second level item for a URL. */
export function getActiveNavigation(pathname: string, search = ''): {
  parentKey: PlatformModule;
  childKey?: string;
} {
  const current = new URLSearchParams(search);
  let parentKey: PlatformModule = getActiveModule(pathname);
  let childKey: string | undefined;

  for (const group of PLATFORM_NAVIGATION) {
    for (const child of group.children) {
      const destination = new URL(child.to, 'http://labelu.local');
      if (destination.pathname !== pathname) {
        continue;
      }

      // `/admin` and `/admin?view=governance` have different children.  A
      // child matches when every query parameter it declares is present with
      // the same value; the first matching child wins.
      const declared = [...destination.searchParams.entries()];
      if (declared.some(([key, value]) => current.get(key) !== value)) {
        continue;
      }

      // Prefer a more specific tab/view match over the bare module URL.
      if (!childKey || declared.length > 0) {
        parentKey = group.key;
        childKey = child.key;
      }
    }
  }

  // The governance surface opens the label library tab by default. Without a
  // tab query parameter the bare `/admin` child would otherwise win.
  if (
    pathname === '/admin' &&
    current.get('view') === 'governance' &&
    !current.get('tab')
  ) {
    parentKey = 'admin';
    childKey = 'admin-labels';
  }

  // Legacy phase-one training surfaces remain addressable for old bookmarks,
  // but they are not part of the daily navigation tree.
  if (
    pathname === '/training' &&
    (current.get('view') === 'reports' || current.get('view') === 'auto-plan')
  ) {
    return { parentKey: 'training', childKey: undefined };
  }

  return { parentKey, childKey };
}


/** Technical surfaces retained for administrators and compatibility links. */
export const INTERNAL_PLATFORM_MODULES: ReadonlyArray<{
  key: Extract<PlatformModule, 'datasets' | 'ingestion' | 'annotation-review'>;
  label: string;
  description: string;
  icon: typeof AppstoreOutlined;
}> = [
  { key: 'datasets', label: '数据集管理', description: '管理训练数据集、标签契约和版本血缘', icon: DatabaseOutlined },
  {
    key: 'ingestion',
    label: '高级数据导入',
    description: '接入 S3、RTSP、第三方平台等外部数据源',
    icon: CloudUploadOutlined,
  },
  {
    key: 'annotation-review',
    label: '质检与抽检',
    description: '处理 AI 预标注、主动学习难例和抽检队列',
    icon: AuditOutlined,
  },
];

/** Advanced technical surfaces retained behind the product modules. */
export const ADVANCED_PLATFORM_MODULES: ReadonlyArray<{
  key: Extract<PlatformModule, 'algorithms' | 'rules' | 'providers'>;
  label: string;
  description: string;
  icon: typeof AppstoreOutlined;
}> = [
  { key: 'algorithms', label: '算法定义', description: '高级配置算法契约、配方和适配器', icon: ExperimentOutlined },
  { key: 'rules', label: '规则中心', description: '高级配置规则模板与运行契约', icon: BranchesOutlined },
  { key: 'providers', label: 'Provider 注册', description: '高级配置预标注与推理能力契约', icon: ApiOutlined },
];

export const MODULE_COPY: Record<PlatformModule, { title: string; description: string; next: string }> = {
  workspace: {
    title: '工作台',
    description: '集中查看本人权限范围内的待办、作业状态与平台告警。',
    next: '按用户权限汇总待办、训练队列和运行告警，快速进入下一步工作。',
  },
  datasets: {
    title: '数据集管理',
    description: '管理训练数据集、标签契约、版本血缘和训练复现证据。',
    next: '数据集供任务和训练复用；标签契约与版本治理仅在系统管理中维护。',
  },
  ingestion: {
    title: '高级数据导入',
    description: '仅用于 S3、RTSP、第三方平台等外部数据接入。',
    next: '普通文件直接在任务与标注中上传；外部来源才需要此页面。',
  },
  'annotation-review': {
    title: '质检与抽检',
    description: '仅处理 AI 预标注、主动学习难例和抽检任务。',
    next: '普通人工标注完成后自动校验并可直接训练，不再强制经过此队列。',
  },
  tasks: {
    title: '任务与标注',
    description: '复用开源任务、样本和标注能力，按任务创建者和协作关系授权。',
    next: '管理自己创建或参与协作的任务、样本和人工标注；训练入口保持统一。',
  },
  algorithms: {
    title: '算法中心',
    description: '配置算法定义、标签契约、模型组和训练适配器。',
    next: '注册算法能力、绑定训练适配器并配置标签契约和质量门禁。',
  },
  rules: {
    title: '规则中心',
    description: '查看规则模板、输入契约和运行时要求。',
    next: '规则校验和版本生效均由服务端规则状态机控制。',
  },
  providers: {
    title: 'Provider 注册',
    description: '查看预标注、推理和评估 Provider 的输入输出契约与可用性。',
    next: '只有具备镜像摘要和工具链版本且通过 HIL 的 Provider 才会报告为可用。',
  },
  training: {
    title: '训练中心',
    description: '编排训练、评估和质量检查，并查看失败原因。',
    next: '普通任务从任务卡片直接开始训练；高级算法契约和适配器配置按需进入算法定义。 ',
  },
  models: {
    title: '模型中心',
    description: '管理训练生成的 PT 模型和 BM1688 边端模型。',
    next: '查看模型版本、PT 权重、BM1688 三种精度制品、真机证据、外部发布和平台下载状态。',
  },
  evolution: {
    title: '进化中心',
    description: '统一管理第三方模型优化请求、数据回流、阈值实验、场景训练、人工审核和发布决策。',
    next: '数据包自动累计、自动标注和自动训练；人工只审核标注质量，并在候选模型不优于上一版本时决定是否发布。',
  },
  admin: {
    title: '系统管理',
    description: '管理成员、权限、标注标签体系和外部服务集成。',
    next: '在标注与数据治理中维护标签库和数据集；数据集标签契约会直接供任务与训练使用。',
  },
  'system-logs': {
    title: '系统日志',
    description: '查看用户操作日志和系统操作日志，快速定位谁在什么时间做了什么。',
    next: '列表只展示简短操作动作，点击详细查看请求路径、状态、耗时和请求内容。',
  },
};

export function getActiveModule(pathname: string): PlatformModule {
  if (pathname.startsWith('/training')) return 'training';
  // The edge queue is an operational surface attached to model validation.
  // Keep it as a direct route without adding another top-level navigation item.
  if (pathname.startsWith('/edge-task-center') || pathname.startsWith('/edge-tasks')) return 'models';
  const match = [...PLATFORM_MODULES, ...INTERNAL_PLATFORM_MODULES, ...ADVANCED_PLATFORM_MODULES].find(({ key }) =>
    pathname.startsWith(`/${key}`),
  );
  return match?.key || 'workspace';
}
