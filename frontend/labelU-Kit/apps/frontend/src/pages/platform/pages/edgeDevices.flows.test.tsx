/**
 * 边端设备管理页（系统管理 → 边端设备管理 /admin?view=edge-devices）点击流程复现测试。
 *
 * 目标：断言「正确行为」，断言失败即为 bug 证据。
 * 覆盖 task-6 的 5 组场景：
 *   1. 新增：弹窗标题 / 必填校验 / 提交 payload / 成功提示 / 关闭与刷新
 *   2. 编辑：表单回填 / PATCH 路径 / 密码留空时 password_secret_action=keep
 *   3. 查看：只读详情，且展示已保存的关键字段（端口、启用状态等）
 *   4. 连接方式切换为「设备程序主动连接」后隐藏 SSH 表单，且不提交旧 SSH 字段
 *   5. 保存失败提示：后端中文 msg 透传；422 detail 不应降级为「操作失败，请稍后重试」
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { message } from 'antd';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { MemoryRouter } from 'react-router-dom';

import request from '@/api/request';
import EdgeDevicesPanel from './edgeDevices';

vi.mock('@/api/request', () => ({
  default: {
    get: vi.fn(),
    post: vi.fn(),
    patch: vi.fn(),
    put: vi.fn(),
    delete: vi.fn(),
  },
}));

const mockedGet = vi.mocked(request.get);
const mockedPost = vi.mocked(request.post);
const mockedPatch = vi.mocked(request.patch);

if (!window.matchMedia) {
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
}

const EDGE_ROOT = '/data/7G_AI/edge';

type AnyRecord = Record<string, any>;

function makeDevice(overrides: AnyRecord = {}): AnyRecord {
  return {
    id: 'edge_1',
    name: '龙岗 1 号设备',
    host: '10.1.1.147',
    port: 2222,
    user: 'linaro',
    enabled: false,
    auto_after_export: false,
    run_coco_eval: true,
    docker_use_sudo: true,
    connect_timeout_sec: 45,
    validation_timeout_sec: 9000,
    docker_container: 'bm1688-test',
    validation_channel: 'ssh',
    password_configured: true,
    password_runtime_available: true,
    private_key_path_configured: false,
    edge_root: EDGE_ROOT,
    environment_status: 'ready',
    environment_message: '环境检查通过',
    environment_report: {
      environment_ready: true,
      architecture_ok: true,
      bm1688_device_ok: true,
      libsophon_ok: true,
      docker_ok: true,
      docker_image_ok: true,
      container_ok: true,
      python_runtime_ok: true,
      validator_ok: true,
    },
    last_probe_checked_at: '2026-10-01T10:00:00+08:00',
    agent_bootstrap_status: 'READY',
    agent_bootstrap_control_plane_enabled: true,
    is_busy: false,
    available_for_dispatch: true,
    is_active: true,
    ...overrides,
  };
}

function mockList(devices: AnyRecord[], activeDeviceId?: string) {
  mockedGet.mockImplementation((async (url: string) => {
    if (url === '/v1/yolo-bm1688-exports/edge-ssh-settings') {
      return { data: { edge_root: EDGE_ROOT } } as never;
    }
    if (url === '/v1/edge-devices') {
      return {
        data: {
          active_device_id: activeDeviceId ?? devices[0]?.id ?? '',
          devices,
          total: devices.length,
          page: 0,
          size: 10,
        },
      } as never;
    }
    throw new Error(`Unexpected GET ${url}`);
  }) as never);
}

function renderPanel() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <MemoryRouter>
      <QueryClientProvider client={queryClient}>
        <EdgeDevicesPanel />
      </QueryClientProvider>
    </MemoryRouter>,
  );
}

/** antd Button 在两个中文字符间自动插入空格（「新 增」），这里按去空白后的文本精确匹配。 */
function buttonsByText(text: string) {
  return screen.getAllByRole('button').filter((button) => (button.textContent || '').replace(/\s/g, '') === text);
}

function clickButton(text: string) {
  const matched = buttonsByText(text);
  if (!matched.length) throw new Error(`未找到文本为「${text}」的按钮`);
  fireEvent.click(matched[matched.length - 1]);
}

async function waitForModalContent(title: string): Promise<HTMLElement> {
  const titleNode = await screen.findByText(title);
  const content = titleNode.closest('.ant-modal-content');
  if (!content) throw new Error(`未找到标题为「${title}」的弹窗内容`);
  return content as HTMLElement;
}

function inputValue(label: string) {
  return (screen.getByLabelText(label) as HTMLInputElement).value;
}

function switchChecked(label: string) {
  const element = screen.getByRole('switch', { name: label });
  return element.getAttribute('aria-checked') === 'true';
}

function checkOn(label: string) {
  if (!switchChecked(label)) fireEvent.click(screen.getByRole('switch', { name: label }));
}

async function selectChannel(optionLabel: string) {
  const current = screen.queryByText('平台通过 SSH 连接');
  if (optionLabel === '平台通过 SSH 连接' && current) return;
  fireEvent.mouseDown(screen.getByRole('combobox', { name: '连接方式' }));
  const options = await screen.findAllByText(optionLabel);
  fireEvent.click(options[options.length - 1]);
}

async function fillSshIdentity(values: { host: string; port: string; user: string; password?: string }) {
  fireEvent.change(screen.getByLabelText('设备地址'), { target: { value: values.host } });
  fireEvent.change(screen.getByLabelText('SSH 端口'), { target: { value: values.port } });
  fireEvent.change(screen.getByLabelText('SSH 账号'), { target: { value: values.user } });
  if (values.password !== undefined) {
    fireEvent.change(screen.getByLabelText('SSH 密码'), { target: { value: values.password } });
  }
}

async function openCreateModal() {
  clickButton('新增设备');
  return waitForModalContent('新增边端设备');
}

async function openEditModal() {
  clickButton('编辑');
  return waitForModalContent('编辑边端设备');
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  mockedGet.mockReset();
  mockedPost.mockReset();
  mockedPatch.mockReset();
});

describe('边端设备管理 · 新增/编辑/查看 点击流程', () => {
  it('新增：点击「新增设备」后弹窗标题应为「新增边端设备」', async () => {
    mockList([]);
    renderPanel();
    await waitFor(() => expect(screen.getByRole('button', { name: /新增设备/ })).toBeTruthy());

    const modal = await openCreateModal();
    expect(within(modal).getByText('新增边端设备')).toBeTruthy();
    // 表单项应齐全
    expect(screen.getByLabelText('设备名称')).toBeTruthy();
    expect(screen.getByLabelText('设备地址')).toBeTruthy();
    expect(screen.getByLabelText('SSH 端口')).toBeTruthy();
    expect(screen.getByLabelText('SSH 账号')).toBeTruthy();
    expect(screen.getByLabelText('SSH 密码')).toBeTruthy();
  });

  it('新增：弹窗应带出 SSH 默认值（连接方式/端口/账号/超时/开关）', async () => {
    mockList([]);
    renderPanel();
    await openCreateModal();
    // 边端工作目录由运行设置接口异步回填
    await waitFor(() => expect(inputValue('边端工作目录')).toBe(EDGE_ROOT));

    const actual = {
      channel: screen.queryByText('平台通过 SSH 连接') ? 'ssh' : '未选中',
      name: inputValue('设备名称'),
      host: inputValue('设备地址'),
      port: inputValue('SSH 端口'),
      user: inputValue('SSH 账号'),
      password: inputValue('SSH 密码'),
      docker_container: inputValue('运行容器名称'),
      connect_timeout_sec: inputValue('连接超时（秒）'),
      validation_timeout_sec: inputValue('检查超时（秒）'),
      edge_root: inputValue('边端工作目录'),
      enabled: switchChecked('启用设备'),
      auto_after_export: switchChecked('编译完成后自动检查'),
      run_coco_eval: switchChecked('执行标准精度评估'),
      docker_use_sudo: switchChecked('容器命令使用管理员权限'),
    };

    expect(actual).toEqual({
      channel: 'ssh',
      name: '',
      host: '',
      port: '22',
      user: 'linaro',
      password: 'qiji24@$@1818',
      docker_container: 'bm1688',
      connect_timeout_sec: '30',
      validation_timeout_sec: '7200',
      edge_root: EDGE_ROOT,
      enabled: true,
      auto_after_export: true,
      run_coco_eval: false,
      docker_use_sudo: true,
    });
  });

  it('新增：必填项为空时点击「新增」应出现对应中文校验提示', async () => {
    mockList([]);
    renderPanel();
    await openCreateModal();

    // 清空有默认值的字段，确保校验被触发
    fireEvent.change(screen.getByLabelText('SSH 端口'), { target: { value: '' } });
    fireEvent.change(screen.getByLabelText('SSH 账号'), { target: { value: '' } });

    clickButton('新增');

    expect(await screen.findByText('请输入设备地址')).toBeTruthy();
    expect(await screen.findByText('请输入 SSH 端口')).toBeTruthy();
    expect(await screen.findByText('请输入 SSH 账号')).toBeTruthy();
    // 密码/私钥可以留空：服务端会回落到部署默认凭据，因此不应再出现必填提示
    expect(screen.queryByText('请输入 SSH 密码或私钥路径')).toBeNull();
    expect(mockedPost).not.toHaveBeenCalled();
  });

  it('新增：完整填写后 POST payload 正确，成功提示、关闭弹窗并刷新列表', async () => {
    mockList([]);
    mockedPost.mockResolvedValue({
      data: { id: 'edge_new', name: '新增测试设备', validation_channel: 'ssh' },
    } as never);
    const successNotice = vi.spyOn(message, 'success').mockImplementation(() => ({}) as never);

    renderPanel();
    const modal = await openCreateModal();
    await selectChannel('平台通过 SSH 连接');

    fireEvent.change(screen.getByLabelText('设备名称'), { target: { value: '新增测试设备' } });
    await fillSshIdentity({ host: '10.1.1.200', port: '2222', user: 'edgeuser', password: 'secret-pass' });
    fireEvent.change(screen.getByLabelText('运行容器名称'), { target: { value: 'bm1688' } });
    fireEvent.change(screen.getByLabelText('连接超时（秒）'), { target: { value: '60' } });
    fireEvent.change(screen.getByLabelText('检查超时（秒）'), { target: { value: '1200' } });
    checkOn('启用设备');
    checkOn('编译完成后自动检查');
    expect(within(modal).getByText('新增边端设备')).toBeTruthy();

    clickButton('新增');
    await waitFor(() => expect(mockedPost).toHaveBeenCalledWith('/v1/edge-devices', expect.any(Object)));

    const call = mockedPost.mock.calls.find(([url]) => url === '/v1/edge-devices');
    if (!call) throw new Error('没有发出创建设备请求');
    const payload = call[1] as AnyRecord;

    expect(payload).toMatchObject({
      name: '新增测试设备',
      host: '10.1.1.200',
      port: 2222,
      user: 'edgeuser',
      password: 'secret-pass',
      docker_container: 'bm1688',
      connect_timeout_sec: 60,
      validation_timeout_sec: 1200,
      enabled: true,
      auto_after_export: true,
      set_active: true,
    });

    await waitFor(() => expect(successNotice).toHaveBeenCalledWith(expect.stringContaining('设备已新增')));
    await waitFor(() => expect(screen.queryByText('新增边端设备')).toBeNull());
    await waitFor(() =>
      expect(mockedGet.mock.calls.filter(([url]) => url === '/v1/edge-devices').length).toBeGreaterThan(1),
    );
  });

  it('编辑：点击行内「编辑」应回填该设备的已保存配置', async () => {
    mockList([makeDevice()]);
    renderPanel();
    await screen.findByText('龙岗 1 号设备');
    await openEditModal();

    const actual = {
      channel: screen.queryByText('平台通过 SSH 连接') ? 'ssh' : '未选中',
      name: inputValue('设备名称'),
      host: inputValue('设备地址'),
      port: inputValue('SSH 端口'),
      user: inputValue('SSH 账号'),
      docker_container: inputValue('运行容器名称'),
      connect_timeout_sec: inputValue('连接超时（秒）'),
      validation_timeout_sec: inputValue('检查超时（秒）'),
      enabled: switchChecked('启用设备'),
      auto_after_export: switchChecked('编译完成后自动检查'),
      run_coco_eval: switchChecked('执行标准精度评估'),
      docker_use_sudo: switchChecked('容器命令使用管理员权限'),
    };

    expect(actual).toEqual({
      channel: 'ssh',
      name: '龙岗 1 号设备',
      host: '10.1.1.147',
      port: '2222',
      user: 'linaro',
      docker_container: 'bm1688-test',
      connect_timeout_sec: '45',
      validation_timeout_sec: '9000',
      enabled: false,
      auto_after_export: false,
      run_coco_eval: true,
      docker_use_sudo: true,
    });
  });

  it('编辑：保存应 PATCH 该设备，密码留空时提交 password_secret_action=keep 且不带 password', async () => {
    mockList([makeDevice()]);
    mockedPatch.mockResolvedValue({
      data: { id: 'edge_1', name: '龙岗 1 号设备', validation_channel: 'ssh' },
    } as never);
    const successNotice = vi.spyOn(message, 'success').mockImplementation(() => ({}) as never);

    renderPanel();
    await screen.findByText('龙岗 1 号设备');
    await openEditModal();

    await selectChannel('平台通过 SSH 连接');
    await fillSshIdentity({ host: '10.9.9.9', port: '2222', user: 'linaro' });

    clickButton('保存');
    await waitFor(() => expect(mockedPatch).toHaveBeenCalledWith('/v1/edge-devices/edge_1', expect.any(Object)));

    const call = mockedPatch.mock.calls.find(([url]) => url === '/v1/edge-devices/edge_1');
    if (!call) throw new Error('没有发出 PATCH /v1/edge-devices/edge_1 请求');
    const payload = call[1] as AnyRecord;

    expect(payload).toMatchObject({
      host: '10.9.9.9',
      password_secret_action: 'keep',
      private_key_path_secret_action: 'keep',
    });
    expect(payload).not.toHaveProperty('password');
    await waitFor(() => expect(successNotice).toHaveBeenCalledWith(expect.stringContaining('设备配置已更新')));
  });

  it('查看：应只读展示详情，且展示端口、启用状态等已保存字段', async () => {
    mockList([makeDevice()]);
    renderPanel();
    await screen.findByText('龙岗 1 号设备');

    clickButton('查看');
    const detail = await waitForModalContent('设备详情 · 龙岗 1 号设备');

    // 只读：详情弹窗不应出现可提交表单控件
    expect(within(detail).queryAllByRole('textbox')).toHaveLength(0);
    expect(within(detail).queryAllByRole('spinbutton')).toHaveLength(0);
    expect(within(detail).queryAllByRole('switch')).toHaveLength(0);

    const requiredLabels = [
      '连接方式',
      '设备地址',
      'SSH 账号',
      'SSH 端口',
      '启用状态',
      '导出后自动检查',
      '连接超时',
      '检查超时',
      'BM1688 环境检查',
    ];
    const missingLabels = requiredLabels.filter((label) => !within(detail).queryByText(label, { exact: true }));
    expect(missingLabels).toEqual([]);

    expect(within(detail).getByText('10.1.1.147')).toBeTruthy();
    expect(within(detail).getByText('linaro')).toBeTruthy();
    expect(within(detail).getByText('45 秒')).toBeTruthy();
    expect(within(detail).getByText('9000 秒')).toBeTruthy();
    // 已保存的环境检查报告应可见
    expect(within(detail).getByText('系统架构')).toBeTruthy();
    expect(within(detail).getAllByText('通过').length).toBeGreaterThan(0);
  });

  it('连接方式切换：选择「设备程序主动连接」后地址/账号/密码仍展示但只读，且不提交 SSH 字段', async () => {
    mockList([]);
    mockedPost.mockResolvedValue({
      data: { id: 'edge_agent', name: '主动连接设备', validation_channel: 'board_agent' },
    } as never);

    renderPanel();
    const modal = await openCreateModal();

    // 先在 SSH 模式下填写，再切换到设备程序主动连接，模拟用户切换连接方式
    await fillSshIdentity({ host: '10.1.1.147', port: '2222', user: 'linaro', password: 'old-password' });
    await selectChannel('设备程序主动连接');

    // 与新增界面保持一致的字段仍然可见，只是只读（切回 SSH 即可编辑）
    expect((screen.getByLabelText('设备地址') as HTMLInputElement).disabled).toBe(true);
    expect((screen.getByLabelText('SSH 端口') as HTMLInputElement).disabled).toBe(true);
    expect((screen.getByLabelText('SSH 账号') as HTMLInputElement).disabled).toBe(true);
    expect((screen.getByLabelText('SSH 密码') as HTMLInputElement).disabled).toBe(true);
    expect((screen.getByLabelText('SSH 私钥路径（服务端）') as HTMLInputElement).disabled).toBe(true);
    // 切换后应出现「设备程序主动连接」说明（选择项文案 + Alert 文案都在弹窗内）
    expect(within(modal).getAllByText('设备程序主动连接').length).toBeGreaterThan(0);

    fireEvent.change(screen.getByLabelText('设备名称'), { target: { value: '主动连接设备' } });
    clickButton('新增');
    await waitFor(() => expect(mockedPost).toHaveBeenCalledWith('/v1/edge-devices', expect.any(Object)));

    const call = mockedPost.mock.calls.find(([url]) => url === '/v1/edge-devices');
    if (!call) throw new Error('没有发出创建设备请求');
    const payload = call[1] as AnyRecord;

    expect(payload.validation_channel).toBe('board_agent');
    const sshFieldsInPayload = Object.fromEntries(
      ['host', 'port', 'user', 'password', 'private_key_path'].map((key) => [key, payload[key]]),
    );
    expect(sshFieldsInPayload).toEqual({
      host: undefined,
      port: undefined,
      user: undefined,
      password: undefined,
      private_key_path: undefined,
    });
  });

  it('创建：清空密码后仍可提交，服务端会使用默认密码', async () => {
    mockList([]);
    mockedPost.mockResolvedValue({
      data: { id: 'edge_default', name: '默认凭据设备', validation_channel: 'ssh' },
    } as never);
    renderPanel();
    await openCreateModal();
    fireEvent.change(screen.getByLabelText('设备地址'), { target: { value: '10.1.1.201' } });
    fireEvent.change(screen.getByLabelText('SSH 密码'), { target: { value: '' } });
    clickButton('新增');

    await waitFor(() => expect(mockedPost).toHaveBeenCalledWith('/v1/edge-devices', expect.any(Object)));
    const call = mockedPost.mock.calls.find(([url]) => url === '/v1/edge-devices');
    if (!call) throw new Error('没有发出创建设备请求');
    const payload = call[1] as AnyRecord;
    expect(payload.user).toBe('linaro');
    expect(payload.password === '' || payload.password === undefined).toBe(true);
    expect(payload.auto_configure_agent).toBe(false);
  });

  it('编辑：设备程序主动连接的设备也展示地址/账号/密码，切换 SSH 后可修改并保存', async () => {
    mockList([
      makeDevice({
        id: 'edge_agent_edit',
        name: '主动连接设备',
        validation_channel: 'board_agent',
        password_configured: false,
        private_key_path_configured: false,
      }),
    ]);
    mockedPatch.mockResolvedValue({
      data: { id: 'edge_agent_edit', name: '主动连接设备', validation_channel: 'ssh' },
    } as never);
    renderPanel();
    await screen.findByText('主动连接设备');
    await openEditModal();

    // 与新增界面字段一致，但 board_agent 下地址等为只读
    expect((screen.getByLabelText('设备地址') as HTMLInputElement).disabled).toBe(true);
    expect(screen.getByLabelText('SSH 密码')).toBeTruthy();
    expect(screen.getByText(/切换为「平台通过 SSH 连接」后即可修改地址、账号和密码/)).toBeTruthy();

    await selectChannel('平台通过 SSH 连接');
    expect((screen.getByLabelText('设备地址') as HTMLInputElement).disabled).toBe(false);
    fireEvent.change(screen.getByLabelText('设备地址'), { target: { value: '10.1.1.188' } });
    clickButton('保存');

    await waitFor(() =>
      expect(mockedPatch).toHaveBeenCalledWith('/v1/edge-devices/edge_agent_edit', expect.any(Object)),
    );
    const call = mockedPatch.mock.calls.find(([url]) => url === '/v1/edge-devices/edge_agent_edit');
    if (!call) throw new Error('没有发出 PATCH 请求');
    const payload = call[1] as AnyRecord;
    expect(payload.validation_channel).toBe('ssh');
    expect(payload.host).toBe('10.1.1.188');
    expect(payload.auto_configure_agent).toBe(false);
  });

  it('提示：保存失败时应展示后端返回的中文 msg', async () => {
    mockList([makeDevice()]);
    const errorNotice = vi.spyOn(message, 'error').mockImplementation(() => ({}) as never);
    mockedPatch.mockRejectedValue({
      response: { status: 400, data: { msg: '设备地址已存在' } },
    });

    renderPanel();
    await screen.findByText('龙岗 1 号设备');
    await openEditModal();
    await selectChannel('平台通过 SSH 连接');
    await fillSshIdentity({ host: '10.1.1.147', port: '2222', user: 'linaro' });

    clickButton('保存');
    await waitFor(() => expect(errorNotice).toHaveBeenCalled());
    expect(errorNotice).toHaveBeenCalledWith('设备地址已存在');
  });

  it('提示：后端中文校验 msg 含「密码/私钥」时也应展示给用户，而不是降级为通用兜底', async () => {
    mockList([makeDevice()]);
    const errorNotice = vi.spyOn(message, 'error').mockImplementation(() => ({}) as never);
    // 该文案来自后端 edge_device_registry 的合法用户-facing 校验（非敏感值）
    mockedPatch.mockRejectedValue({
      response: { status: 400, data: { msg: 'SSH 通道必须配置 SSH 密码或私钥路径' } },
    });

    renderPanel();
    await screen.findByText('龙岗 1 号设备');
    await openEditModal();
    await selectChannel('平台通过 SSH 连接');
    await fillSshIdentity({ host: '10.1.1.147', port: '2222', user: 'linaro' });

    clickButton('保存');
    await waitFor(() => expect(errorNotice).toHaveBeenCalled());
    expect(errorNotice).toHaveBeenCalledWith('SSH 通道必须配置 SSH 密码或私钥路径');
  });

  it('提示：422（FastAPI detail 数组）时不应只显示「操作失败，请稍后重试」', async () => {
    mockList([makeDevice()]);
    const errorNotice = vi.spyOn(message, 'error').mockImplementation(() => ({}) as never);
    mockedPatch.mockRejectedValue({
      response: {
        status: 422,
        data: {
          detail: [
            { loc: ['body', 'port'], msg: 'ensure this value is less than or equal to 65535', type: 'value_error' },
          ],
        },
      },
    });

    renderPanel();
    await screen.findByText('龙岗 1 号设备');
    await openEditModal();
    await selectChannel('平台通过 SSH 连接');
    await fillSshIdentity({ host: '10.1.1.147', port: '2222', user: 'linaro' });

    clickButton('保存');
    await waitFor(() => expect(errorNotice).toHaveBeenCalled());

    const shownText = String(errorNotice.mock.calls[0]?.[0] ?? '');
    // 期望：至少透出后端参数校验信息；不应是无关的通用兜底文案
    expect(shownText).not.toBe('操作失败，请稍后重试');
    expect(shownText.length).toBeGreaterThan(0);
  });

  it('编辑：把运行容器名称清空后应提交空串（宿主机运行），不能被强制改回 bm1688', async () => {
    mockList([makeDevice({ docker_container: 'bm1688-test' })]);
    mockedPatch.mockResolvedValue({
      data: { id: 'edge_1', name: '龙岗 1 号设备', validation_channel: 'ssh' },
    } as never);

    renderPanel();
    await screen.findByText('龙岗 1 号设备');
    await openEditModal();
    fireEvent.change(screen.getByLabelText('运行容器名称'), { target: { value: '' } });

    clickButton('保存');
    await waitFor(() => expect(mockedPatch).toHaveBeenCalledWith('/v1/edge-devices/edge_1', expect.any(Object)));

    const call = mockedPatch.mock.calls.find(([url]) => url === '/v1/edge-devices/edge_1');
    if (!call) throw new Error('没有发出 PATCH /v1/edge-devices/edge_1 请求');
    expect((call[1] as AnyRecord).docker_container).toBe('');
  });

  it('编辑：原本在宿主机运行的设备（docker_container 为空）打开编辑不应被回填成 bm1688', async () => {
    mockList([makeDevice({ docker_container: '' })]);
    renderPanel();
    await screen.findByText('龙岗 1 号设备');
    await openEditModal();
    expect(inputValue('运行容器名称')).toBe('');
  });

  it('当前设备不在本页时，应提示「当前设备不在本页」而不是「尚未设置当前设备」', async () => {
    mockList([makeDevice({ id: 'edge_page_2', name: '第二页设备', is_active: false })], 'edge_elsewhere');
    renderPanel();
    await screen.findByText('第二页设备');

    expect(await screen.findByText('当前设备不在本页')).toBeTruthy();
    expect(screen.queryByText('尚未设置当前设备')).toBeNull();
    expect(screen.getByText(/当前设备为 edge_elsewhere/)).toBeTruthy();
  });
});
