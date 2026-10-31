import type { EnumerableAttribute, ILabel, TextAttribute } from '@labelu/interface';

export interface AttachmentDeleteCommand {
  /** Attachment Ids description: attachment file id */
  attachment_ids: number[];
}

export interface AttachmentResponse {
  /** Id description: upload file id */
  id?: number;
  filename?: string;
  /** Url description: upload file url */
  url?: string;
  thumbnail_url?: string | null;
  stream_url?: string | null;
  annotation_display_url?: string | null;
  original_width?: number | null;
  original_height?: number | null;
  storage_backend?: string | null;
}

export interface GetUsersApiV1UsersGetParams {
  page?: number;
  size?: number;
  username?: string;
  /** Server-side keyword search across user id, account and account type. */
  q?: string;
}

export interface UserResponse {
  /** Id */
  id?: number;
  /** Username */
  username?: string;
  /** 任务超级用户（管理员），可查看标注统计等 */
  is_task_superuser?: boolean;
}

export interface PreAnnotationFileResponse extends AttachmentResponse {
  sample_names: string[];
}

export interface BasicConfigCommand {
  /** Name description: task name */
  name: string;
  media_type?: MediaType;
  /** Description description: task description */
  description?: string;
  /** Tips description: task tips */
  tips?: string;
  dataset_category_id?: number;
  dataset_id?: number;
  dataset_label_contract_id?: number;
  task_label_scope_mode?: 'FULL' | 'SUBSET';
}

export interface BodyCreateApiV1TasksTaskIdAttachmentsPost {
  /** File */
  file: string;
}

export interface CommonDataResp {
  /** Ok */
  ok: boolean;
}

export interface CreateApiV1TasksTaskIdAttachmentsPostParams {
  task_id: number;
}

export interface CreateApiV1TasksTaskIdSamplesPostParams {
  task_id: number;
}

export interface SampleData {
  id?: number;
  state?: SampleState;
  result: string;
  training_sample_type?: 'negative';
}

export interface CreateSampleCommand {
  /** Data description: sample data, include filename, file url, or result */
  data?: SampleData;
}

export interface CopySampleToTaskCommand {
  source_task_id: number;
  source_sample_id: number;
}

export interface CreateSampleResponse {
  /** Ids description: attachment ids */
  ids?: number[];
}

export interface DeleteApiV1TasksTaskIdAttachmentsDeleteParams {
  task_id: number;
}

export interface DeleteApiV1TasksTaskIdDeleteParams {
  task_id: number;
}

export interface DeletePreAnnotationFileParams {
  task_id: number;
  file_id: number;
}

export interface DeleteSampleCommand {
  /** Sample Ids description: attachment file id */
  sample_ids: number[];
}

export interface DownloadAttachmentApiV1TasksAttachmentFilePathGetParams {
  file_path: string;
}

export interface ExportApiV1TasksTaskIdSamplesExportPostParams {
  task_id: number;
  export_type: ExportType;
}

export interface AutoLabelCommand {
  overwrite?: boolean;
  overwrite_existing_annotations?: boolean;
  template_id?: number | null;
  prompt?: string | null;
  filter_by_labels?: boolean;
  include_geometry_tools?: boolean;
}

export interface AutoLabelResponse {
  status: string;
  task_id: number;
  sample_id: number;
  media_type: MediaType;
  provider: string;
  model?: string | null;
  latency_ms?: number | null;
  pre_annotation_id?: number | null;
  warning_message?: string | null;
}

export interface BatchAutoLabelCommand {
  mode?: 'TAGS_ONLY' | 'BOX_AND_TAGS';
  filter_by_labels?: boolean;
  include_geometry_tools?: boolean;
  include_done_samples?: boolean;
  overwrite_existing_annotations?: boolean;
  sample_states?: Array<'NEW' | 'INCOMPLETE' | 'DONE'>;
  skip_complete_global_tags?: boolean;
}

export interface AutoLabelJobResponse {
  id: number;
  task_id: number;
  status: 'PENDING' | 'PROCESSING' | 'COMPLETED' | 'FAILED' | 'CANCELLED';
  sample_count: number;
  processed_count: number;
  success_count: number;
  failed_count: number;
  error_message?: string | null;
  created_at?: string | null;
}

export interface SampleBoundsCheckJobResponse {
  id: number;
  task_id: number;
  status: 'PENDING' | 'PROCESSING' | 'COMPLETED' | 'FAILED';
  sample_count: number;
  processed_count: number;
  normal_count: number;
  incomplete_count: number;
  abnormal_count: number;
  failed_count: number;
  error_message?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface YoloPreannotateModelResponse {
  kind: 'imported' | 'trained';
  id: number;
  title: string;
  detail?: string;
  category?: string;
  imgsz?: number | null;
  recommended_conf?: number | null;
  matched_labels?: string[];
  missing_task_labels?: string[];
  extra_model_labels?: string[];
}

export interface TagtoolPreannotateModelResponse {
  kind: 'imported';
  id: number;
  title: string;
  detail?: string;
  category?: string;
  label_heads?: string[];
}

export interface PreannotateModelsResponse<T> {
  items: T[];
}

export interface PreannotateJobResponse {
  id: number;
  task_id: number;
  status: 'PENDING' | 'PROCESSING' | 'COMPLETED' | 'FAILED';
  sample_count: number;
  processed_count: number;
  success_count: number;
  annotated_count?: number;
  empty_count?: number;
  failed_count: number;
  model_kind?: string;
  model_id?: number;
  model?: string | null;
  error_message?: string | null;
  created_at?: string | null;
}

export interface PreannotationCapabilityItem {
  key: string;
  label?: string;
  kind?: string;
  visible?: boolean;
  available?: boolean;
  ready?: boolean;
  reason?: string | null;
  mode?: string;
  supports_batch?: boolean;
  supports_sample?: boolean;
  model_count?: number;
  config_id?: number | null;
  endpoint?: string | null;
  models?: unknown[];
}

export interface PreannotationCapabilitiesResponse {
  items: PreannotationCapabilityItem[];
}

export interface YoloPreannotateCommand {
  model_kind: 'imported' | 'trained';
  model_id: number;
  overwrite?: boolean;
  include_done_samples?: boolean;
  sample_ids?: number[];
  sample_id?: number;
  conf?: number;
  imgsz?: number;
  agnostic_nms?: boolean;
  apply_to_canvas_overwrite?: boolean;
}

export interface TagtoolPreannotateCommand {
  model_kind?: 'imported';
  model_id: number;
  overwrite?: boolean;
  include_done_samples?: boolean;
  sample_ids?: number[];
  sample_id?: number;
}

export interface ProviderPreannotateCommand {
  filter_by_labels?: boolean;
  target_labels?: string[];
  include_done_samples?: boolean;
  overwrite?: boolean;
  sample_ids?: number[];
  sample_id?: number;
}

export interface ExportSampleCommand {
  /** Sample Ids description: sample id */
  sample_ids?: number[];
}

export interface LabelDatasetImportJobResponse {
  id: number;
  task_id: number;
  export_type: string;
  status: 'PENDING' | 'PROCESSING' | 'COMPLETED' | 'FAILED';
  sample_count: number;
  processed_count: number;
  skipped_count?: number | null;
  warning_message?: string | null;
  error_message?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
}

export enum ExportType {
  JSON = 'JSON',
  MASK = 'MASK',
  COCO = 'COCO',
  YOLO = 'YOLO',
  CSV = 'CSV',
  XML = 'XML',
  LABEL_ME = 'LABEL_ME',
  TF_RECORD = 'TF_RECORD',
  PASCAL_VOC = 'PASCAL_VOC',
}

export interface GetApiV1TasksTaskIdGetParams {
  task_id: number;
}

export interface GetApiV1TasksTaskIdSamplesSampleIdGetParams {
  task_id: number;
  sample_id: number;
}

export interface GetPreApiV1TasksTaskIdSamplesSampleIdPreGetParams {
  task_id: number;
  sample_id: number;
}

export interface HTTPValidationError {
  /** Detail */
  detail?: ValidationError[];
}

export interface ListByApiV1TasksGetParams {
  page?: number;
  size?: number;
  status?: TaskStatus;
  media_type?: MediaType;
  rect_preset_slug?: string;
  /** Current task contract must cover all selected global label IDs. */
  label_ids?: number | number[];
  /** true=可训练 false=未达标 */
  training_ready?: boolean;
  dataset_category_id?: number;
  uncategorized?: boolean;
  dataset_id?: number;
  dataset_label_contract_id?: number;
  dataset_search?: string;
  q?: string;
}

export interface ListByApiV1TasksTaskIdSamplesGetParams {
  task_id: number;
  after?: number;
  before?: number;
  page?: number;
  size?: number;
  sort?: string;
  state?: SampleState;
  /** Server-side search by sample filename or numeric sample id. */
  q?: string;
}

export interface LoginCommand {
  /** Username */
  username: string;
  /** Password */
  password: string;
}

export interface LoginResponse {
  /** Token description: user credential */
  token: string;
}

export interface LogoutResponse {
  /** Msg */
  msg: string;
}

export enum MediaType {
  IMAGE = 'IMAGE',
  VIDEO = 'VIDEO',
  AUDIO = 'AUDIO',
  // TODO: 后续支持
  // POINT_CLOUD = 'POINT_CLOUD',
}

export interface MetaData {
  /** Total */
  total: number;
  /** Page */
  page?: number;
  /** Size */
  size: number;
}

export interface OkRespAttachmentResponse {
  data: AttachmentResponse;
}

export interface OkRespCommonDataResp {
  data: CommonDataResp;
}

export interface OkRespCreateSampleResponse {
  data: CreateSampleResponse;
}

export interface OkRespLoginResponse {
  data: LoginResponse;
}

export interface OkRespUserInfo {
  data: SignupResponse;
}

export interface OkRespLogoutResponse {
  data: LogoutResponse;
}

export interface OkRespSampleResponse {
  data: SampleResponse;
}

export interface OkRespAutoLabelResponse {
  data: AutoLabelResponse;
}

export interface OkRespSignupResponse {
  data: SignupResponse;
}

export interface OkRespTaskResponse {
  data: TaskResponse;
}

export interface OkRespTaskResponseWithStatics {
  data: TaskResponseWithStatics;
}

export interface PatchSampleCommand {
  /** Data description: sample data, include filename, file url, or result */
  data?: SampleData;
  /** Annotated Count description: annotate result count */
  annotated_count?: number;
  /** description: sample file state, must be 'SKIPPED', 'NEW', or None */
  state?: SampleState;
  /** Version timestamp returned when the sample was loaded. */
  expected_updated_at?: string;
  /** Task label contract revision loaded by the annotation page. */
  expected_task_contract_revision?: number;
}

export interface SampleResponse {
  /** Id description: annotation id */
  id?: number;
  inner_id: number;
  is_pre_annotated: boolean;
  is_yolo_pre_annotated?: boolean;
  is_vlx_pre_annotated?: boolean;
  is_experiment_dataset_pre_annotated?: boolean;

  /** State description: sample file state, NEW is has not start yet, DONE is completed, SKIPPED is skipped */
  state?: SampleState;
  /** Data description: sample data, include filename, file url, or result */
  data?: SampleData;
  file: {
    id: number;
    url: string;
    filename: string;
    thumbnail_url?: string | null;
    stream_url?: string | null;
    annotation_display_url?: string | null;
    original_width?: number | null;
    original_height?: number | null;
    storage_backend?: string | null;
  };
  /** Annotated Count description: annotate result count */
  annotated_count?: number;
  /** Created At description: task created at time */
  created_at?: string;
  /** Created By description: task created by */
  created_by?: UserResp;
  /** Updated At description: task updated at time */
  updated_at?: string;
  /** Updated By description: task updated by */
  updated_by?: UserResp;
}

export interface PreAnnotationResponse {
  /** Id description: annotation id */
  id?: number;
  /** Data description: sample data, include filename, file url, or result */
  data?: PreAnnotationType[];
  file?: {
    id: string;
    url: string;
    filename: string;
    thumbnail_url?: string | null;
    stream_url?: string | null;
    annotation_display_url?: string | null;
    original_width?: number | null;
    original_height?: number | null;
    storage_backend?: string | null;
  } | null;
  /** Created At description: task created at time */
  created_at?: string;
  /** Created By description: task created by */
  created_by?: UserResp;
  /** Updated At description: task updated at time */
  updated_at?: string;
  /** Updated By description: task updated by */
  updated_by?: UserResp;
}

export interface SampleListResponse {
  meta_data?: MetaData;
  /** Data */
  data: SampleResponse[];
}

export interface AttachmentListResponse {
  meta_data?: MetaData;
  /** Data */
  data: AttachmentResponse[];
}

export interface PreAnnotationListResponse {
  meta_data?: MetaData;
  /** Data */
  data: PreAnnotationResponse[];
}

export interface OkResponse<T> {
  data: T;
}

export interface ListResponseWithMeta<T> {
  meta_data?: MetaData;
  data: T[];
}

export enum SampleState {
  NEW = 'NEW',
  SKIPPED = 'SKIPPED',
  DONE = 'DONE',
  ABNORMAL = 'ABNORMAL',
  INCOMPLETE = 'INCOMPLETE',
}

export interface SignupCommand {
  /** Username */
  username: string;
  /** Password */
  password: string;
}

export interface SignupResponse {
  /** Id */
  id: number;
  /** Username */
  username: string;
}

export interface TaskAnnotationAuditSummary {
  total_samples?: number;
  passed_samples?: number;
  failed_samples_count?: number;
  failed_missing_tags_count?: number;
  failed_missing_rect_count?: number;
  failed_incomplete_count?: number;
  failed_geometry_count?: number;
}

export interface TaskResponse {
  /** Id description: task id */
  id?: number;
  /** 7GAI project boundary */
  project_id?: number | null;
  /** Name description: task name */
  name?: string;
  /** Description description: task description */
  description?: string;
  /** Tips description: task tips */
  tips?: string;
  /** Config description: task config content */
  config?: string;
  /** Media Type description: task media type: IMAGE, VIDEO, AUDIO */
  media_type?: MediaType;
  /** Status description: task status: DRAFT, IMPORTED, CONFIGURED, INPROGRESS, FINISHED */
  status?: TaskStatus;

  stats: TaskStatics;
  /** IMAGE task: global tags + valid rects on every sample */
  training_ready?: boolean | null;
  annotation_audit?: TaskAnnotationAuditSummary | null;
  /** Created At description: task created at time */
  created_at?: string;
  /** Created By description: task created at time */
  created_by?: UserResp;
  source_type?: 'internal' | 'third_party';
  source_system?: string | null;
  source_external_task_id?: string | null;
  source_external_package_id?: string | null;
  source_integration_id?: number | null;
  current_annotation_contract_id?: number | null;
  annotation_contract_revision?: number | null;
  label_set_signature?: string | null;
  class_order_signature?: string | null;
  dataset_category_id?: number | null;
  dataset_category_key?: string | null;
  dataset_category_name?: string | null;
  dataset_category_status?: string | null;
  dataset_id?: number | null;
  dataset_label_contract_id?: number | null;
  task_label_scope_mode?: 'FULL' | 'SUBSET' | string;
}

export interface TaskResponseWithStatics {
  /** Id description: task id */
  id?: number;
  /** 7GAI project boundary */
  project_id?: number | null;
  /** Name description: task name */
  name?: string;
  /** Description description: task description */
  description?: string;
  /** Tips description: task tips */
  tips?: string;
  /** Config description: task config content */
  config?: string;
  /** Media Type description: task media type: IMAGE, VIDEO, AUDIO */
  media_type?: MediaType;
  /** Status description: task status: DRAFT, IMPORTED, CONFIGURED, INPROGRESS, FINISHED */
  status?: TaskStatus;
  /** Created At description: task created at time */
  created_at?: string;
  /** Created By description: task created at time */
  created_by?: UserResp;
  source_type?: 'internal' | 'third_party';
  source_system?: string | null;
  source_external_task_id?: string | null;
  source_external_package_id?: string | null;
  source_integration_id?: number | null;
  updaters?: UserResp[];
  stats?: TaskStatics;
  training_ready?: boolean | null;
  annotation_audit?: TaskAnnotationAuditSummary | null;
  current_annotation_contract_id?: number | null;
  annotation_contract_revision?: number | null;
  label_set_signature?: string | null;
  class_order_signature?: string | null;
  dataset_category_id?: number | null;
  dataset_category_key?: string | null;
  dataset_category_name?: string | null;
  dataset_category_status?: string | null;
  dataset_id?: number | null;
  dataset_label_contract_id?: number | null;
  task_label_scope_mode?: 'FULL' | 'SUBSET' | string;
}

export enum TaskStatus {
  DRAFT = 'DRAFT',
  IMPORTED = 'IMPORTED',
  CONFIGURED = 'CONFIGURED',
  INPROGRESS = 'INPROGRESS',
  FINISHED = 'FINISHED',
}

export interface TaskListResponseWithStatics {
  meta_data?: MetaData;
  /** Data */
  data: TaskResponseWithStatics[];
}

export interface TaskListWithHeadersResponse {
  data: TaskListResponseWithStatics;
  headers?: Record<string, string>;
}

export interface TaskRectPresetSummaryItem {
  slug: string;
  name: string;
  count: number;
}

export interface TaskRectPresetSummaryResponse {
  all: number;
  presets: TaskRectPresetSummaryItem[];
  uncategorized: number;
}

export interface OkRespTaskRectPresetSummaryResponse {
  data: TaskRectPresetSummaryResponse;
}

export interface TaskStatics {
  /** New description: count for task data have not labeled yet */
  new?: number;
  /** Done description: count for task data already labeled */
  done?: number;
  /** Skipped description: count for task data skipped */
  skipped?: number;
  /** Abnormal description: count for task data with abnormal annotations */
  abnormal?: number;
  /** Incomplete description: count for task data with incomplete annotations */
  incomplete?: number;
}

export interface UpdateApiV1TasksTaskIdPatchParams {
  task_id: number;
}

export interface UpdateApiV1TasksTaskIdSamplesSampleIdPatchParams {
  task_id: number;
  sample_id: number;
}

export interface UpdateCommand {
  /** Name description: task name */
  name?: string;
  /** Description description: task description */
  description?: string;
  /** Tips description: task tips */
  tips?: string;
  /** description: task config content */
  media_type?: MediaType;
  /** Config description: task config content */
  config?: string;
  dataset_category_id?: number | null;
  dataset_id?: number | null;
  dataset_label_contract_id?: number | null;
  task_label_scope_mode?: 'FULL' | 'SUBSET';
}

export interface UserResp {
  /** Id */
  id?: number;
  /** Username */
  username?: string;
}

export interface ValidationError {
  /** Location */
  loc: any[];
  /** Message */
  msg: string;
  /** Error Type */
  type: string;
}

interface TextTool {
  /**
   * 唯一标识
   */
  id?: string;
  /**
   * 文本类型: text（文本）
   */
  type?: 'text';
  /**
   * 文本内容
   */
  value?: Record<string, string>;
}

interface TagTool {
  /**
   * 唯一标识
   */
  id?: string;
  type?: 'tag';
  value?: Record<string, string[]>;
}

export interface ParsedResult {
  pointTool?: PointResult;
  rectTool?: RectResult;
  polygonTool?: PolygonResult;
  lineTool?: LineResult;
  cuboidTool?: CuboidResult;
  videoSegmentTool?: VideoSegmentResult;
  videoFrameTool?: VideoFrameResult;
  audioSegmentTool?: AudioSegmentResult;
  audioFrameTool?: AudioFrameResult;
  textTool?: TextResult;
  tagTool?: TagResult;
}

export interface PointResult {
  toolName?: 'pointTool';
  result?: PointTool[];
}

export interface RectResult {
  toolName?: 'rectTool';
  result?: RectTool[];
}

export interface PolygonResult {
  toolName?: 'polygonTool';
  result?: PolygonTool[];
}

export interface LineResult {
  toolName?: 'lineTool';
  result?: GeneratedSchemaForRoot[];
}

export interface CuboidResult {
  toolName?: 'cuboidTool';
  result?: CuboidTool[];
}

export interface VideoSegmentResult {
  toolName?: 'videoSegmentTool';
  result?: SegmentTool[];
}

export interface VideoFrameResult {
  toolName?: 'videoFrameTool';
  result?: FrameTool[];
}

export interface AudioSegmentResult {
  toolName?: 'audioSegmentTool';
  result?: SegmentTool[];
}

export interface AudioFrameResult {
  toolName?: 'audioFrameTool';
  result?: FrameTool[];
}

export interface TextResult {
  toolName?: 'textTool';
  result?: TextTool[];
}

export interface TagResult {
  toolName?: 'tagTool';
  result?: TagTool[];
}

export interface PreAnnotationType {
  /**
   * The name of the sample file.
   */
  sample_name: string;
  config: {
    pointTool?: ILabel[];
    rectTool?: ILabel[];
    polygonTool?: ILabel[];
    lineTool?: ILabel[];
    cuboidTool?: ILabel[];
    videoSegmentTool?: ILabel[];
    videoFrameTool?: ILabel[];
    audioSegmentTool?: ILabel[];
    audioFrameTool?: ILabel[];
    textTool?: TextAttribute[];
    tagTool?: EnumerableAttribute[];
  };
  meta_data?: {
    width?: number;
    height?: number;
    rotate?: number;
    duration?: number;
  };
  annotations: {
    pointTool?: PointResult;
    rectTool?: RectResult;
    polygonTool?: PolygonResult;
    lineTool?: LineResult;
    cuboidTool?: CuboidResult;
    videoSegmentTool?: VideoSegmentResult;
    videoFrameTool?: VideoFrameResult;
    audioSegmentTool?: AudioSegmentResult;
    audioFrameTool?: AudioFrameResult;
    textTool?: TextResult;
    tagTool?: TagResult;
  };
}

interface PointTool {
  /**
   * 唯一标识
   */
  id: string;
  /**
   * x坐标
   */
  x: number;
  /**
   * y坐标
   */
  y: number;
  /**
   * 是否可见
   */
  visible?: boolean;
  attributes?: Attribute;
  /**
   * 标注顺序
   */
  order: number;
  /**
   * 标注类别
   */
  label: string;
}
/**
 * 类别属性，键值对
 */
export type Attribute = Record<string, string | string[]>;
export interface RectTool {
  /**
   * 唯一标识
   */
  id: string;
  /**
   * 拉框左上角x坐标
   */
  x: number;
  /**
   * 拉框左上角y坐标
   */
  y: number;
  /**
   * 拉框宽度
   */
  width: number;
  /**
   * 拉框高度
   */
  height: number;
  /**
   * 是否可见
   */
  visible?: boolean;
  attributes?: Attribute;
  /**
   * 标注顺序
   */
  order: number;
  /**
   * 标注类别
   */
  label: string;
}
export interface PolygonTool {
  /**
   * 唯一标识
   */
  id: string;
  /**
   * 线条类型: line（直线），spline（曲线）
   */
  lineType: 'line' | 'spline';
  /**
   * 控制点列表
   */
  controlPoints?: Point[];
  /**
   * 线条点列表
   */
  points: Point[];
  /**
   * 是否可见
   */
  visible?: boolean;
  attributes?: Attribute;
  /**
   * 标注顺序
   */
  order: number;
  /**
   * 标注类别
   */
  label: string;
}
export interface Point {
  /**
   * x坐标
   */
  x: number;
  /**
   * y坐标
   */
  y: number;
}
export interface GeneratedSchemaForRoot {
  /**
   * 唯一标识
   */
  id: string;
  /**
   * 线条类型: line（直线），spline（曲线）
   */
  lineType: 'line' | 'spline';
  /**
   * 控制点列表
   */
  controlPoints?: Point[];
  /**
   * 线条点列表
   */
  points: Point[];
  /**
   * 是否可见
   */
  visible?: boolean;
  attributes?: Attribute;
  /**
   * 标注顺序
   */
  order: number;
  /**
   * 标注类别
   */
  label: string;
}
export interface CuboidTool {
  /**
   * 唯一标识
   */
  id: string;
  /**
   * 正面方向: front（前面），back（后面），left（左侧面），right（右侧面）
   */
  direction: string;
  /**
   * 正面四个点坐标
   */
  front: {
    /**
     * 左上角坐标
     */
    tl: {
      x: number;
      y: number;
    };
    /**
     * 右上角坐标
     */
    tr: {
      x: number;
      y: number;
    };
    /**
     * 右下角坐标
     */
    br: {
      x: number;
      y: number;
    };
    /**
     * 左下角坐标
     */
    bl: {
      x: number;
      y: number;
    };
  };
  /**
   * 背面四个点坐标
   */
  back: {
    /**
     * 左上角坐标
     */
    tl: {
      x: number;
      y: number;
    };
    /**
     * 右上角坐标
     */
    tr: {
      x: number;
      y: number;
    };
    /**
     * 右下角坐标
     */
    br: {
      x: number;
      y: number;
    };
    /**
     * 左下角坐标
     */
    bl: {
      x: number;
      y: number;
    };
  };
  /**
   * 是否可见
   */
  visible?: boolean;
  attributes?: Attribute;
  /**
   * 标注顺序
   */
  order: number;
  /**
   * 标注类别
   */
  label: string;
}
export interface SegmentTool {
  /**
   * 唯一标识
   */
  id: string;
  /**
   * 时间点
   */
  time?: number;
  /**
   * 标注顺序
   */
  order: number;
  /**
   * 标注类别
   */
  label: string;
  attributes?: Attribute;
}
export interface FrameTool {
  /**
   * 唯一标识
   */
  id: string;
  /**
   * 时间点
   */
  time?: number;
  /**
   * 标注顺序
   */
  order: number;
  /**
   * 标注类别
   */
  label: string;
  attributes?: Attribute;
}

// ── Data Source (S3) ────────────────────────────────────────────────

export interface DataSourceResponse {
  id: number;
  name: string;
  type: string;
  endpoint?: string;
  region?: string;
  bucket: string;
  prefix?: string;
  path_style?: boolean;
  use_ssl?: boolean;
  presign_expire_secs?: number;
  created_by?: number;
  created_at?: string;
  updated_at?: string;
}

export interface CreateDataSourceCommand {
  name: string;
  type?: string;
  endpoint?: string;
  region?: string;
  bucket: string;
  prefix?: string;
  access_key_id: string;
  secret_access_key: string;
  path_style?: boolean;
  use_ssl?: boolean;
  presign_expire_secs?: number;
}

export interface UpdateDataSourceCommand {
  name?: string;
  endpoint?: string;
  region?: string;
  bucket?: string;
  prefix?: string;
  access_key_id?: string;
  secret_access_key?: string;
  path_style?: boolean;
  use_ssl?: boolean;
  presign_expire_secs?: number;
}

export interface ListDataSourcesParams {
  page?: number;
  size?: number;
  q?: string;
}

export interface DataSourceListResponse {
  meta_data?: MetaData;
  data: DataSourceResponse[];
}

export interface S3ObjectItem {
  key: string;
  size: number;
  last_modified?: string | null;
}

export interface S3ObjectListResponse {
  objects: S3ObjectItem[];
  next_page_token?: string | null;
  truncated: boolean;
}

export interface ListS3ObjectsParams {
  ds_id: number;
  prefix?: string;
  extension?: string;
  page_token?: string | null;
  size?: number;
}

export interface ImportS3SamplesCommand {
  data_source_id: number;
  object_keys?: string[];
  prefix?: string;
  extension?: string;
}
