import {
  ApiOutlined,
  AppstoreOutlined,
  AuditOutlined,
  BranchesOutlined,
  CheckCircleOutlined,
  CloudUploadOutlined,
  DatabaseOutlined,
  ExperimentOutlined,
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
  | 'admin';

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
];

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
    description: '查看第三方问题样本的自动对齐、导入、去重、分流和训练触发。',
    next: '正常数据包由系统自动推进，管理人员只处理异常、规则和少量抽查。',
  },
  admin: {
    title: '系统管理',
    description: '管理成员、权限、标注标签体系和外部服务集成。',
    next: '在标注与数据治理中维护标签库和数据集；数据集标签契约会直接供任务与训练使用。',
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
