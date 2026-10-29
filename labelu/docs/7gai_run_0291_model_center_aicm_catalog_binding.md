# RUN-0290 模型中心 AICM 编号与平台下载目录绑定

## 1. 目标与菜单按钮

- 一级菜单：模型中心。
- 相关页签：模型版本、发布记录、下载目录。
- 相关按钮：发布模型、查看发布记录、平台下载、禁止后续下载。
- 两类用户：标注人员和管理人员都可以发布；管理人员额外负责下载禁用。
- 主责：R01 产品、R04 后端、R05 外部平台、R06 模型算法、R08 前端/UI、R09 测试；协作 R03 架构、R07 BM1688、R10 运维。

## 2. 本轮交付

### 发布请求契约

`PublishModelVersionCommand` 新增：

- `aicm_model_set_id`：AICM 返回的稳定模型编号。
- `aicm_environment_id`：AICM 环境标识，避免不同环境的同数字编号串模型。
- `package_path`：已有发布包的可选路径；不提供时由平台从 READY 制品自动生成。
- `published_catalog_id` 旧字段继续兼容，但不能和 AICM 编号同时传入。

模型中心 React 页面已增加居中“发布模型到 AICM”弹窗：标注人员和管理人员都必须填写 AICM 模型编号、环境标识；已有平台包路径可选，留空由服务端自动生成。

### 平台保留包

当模型满足训练、泛化、编译、边端、上线五项门禁，并具备 BM1688 FP32、FP16、INT8 三个 READY BModel 时，服务端：

1. 读取当前模型版本下的 READY PT、ONNX、BModel 和清单制品。
2. 校验制品路径、文件存在性、大小和 SHA256。
3. 自动生成包含制品和 `model_manifest.json` 的 ZIP。
4. 原子写入 `published_model_packages/<AICM模型编号>/model.zip`。
5. 创建或更新 `PublishedModelCatalog`，保留 AICM 编号、本平台下载路径、包摘要和发布人。
6. 回写 `ModelVersion.published_catalog_id`，形成模型版本到 AICM 编号到专家回流的血缘。

已存在同一 AICM 编号但包内容不同的记录时，返回 409，绝不覆盖长期保留包。

### 不同发布边界

- 带 AICM 编号：表示外部发布结果已经取得编号，本次动作负责登记和保留本平台下载包。
- 不带 AICM 编号：保留历史“仅平台模型版本发布”兼容语义，不能宣称已发布到 AICM。
- 外部平台已下载的文件不回滚；模型有问题时只关闭本平台后续下载并发布替代模型。

## 3. 幂等、并发与失败关闭

- 目录以 AICM 环境 + AICM 模型编号作为唯一身份；同编号重放返回同一目录，不重新覆盖文件。
- 临时 ZIP 使用唯一临时文件，平台包通过硬链接/原子安装避免并发覆盖。
- 包摘要、大小和清单制品摘要不一致时发布失败关闭。
- 任何一个质量门禁、固定评估证据、三精度 BModel 或真实边端证据缺失时禁止发布。

## 4. 验证证据

- `python -m pytest -q labelu/tests/internal/adapter/routers/test_model_version.py`：7 项通过。
- 新增 AICM 编号发布回归验证：模型发布后可查询 `/api/v1/published-models/{model_id}`，`local_package_available=true`，下载接口返回 ZIP。
- `pnpm exec vitest run src/pages/platform/pages/models.test.tsx`：11 项通过；覆盖发布弹窗填写编号和环境后提交请求。
- `pnpm run type-check`：通过。
- `pnpm run build`：通过；保留既有 Ant Design `use client` 和大包体积警告，不作为失败。
- 设备通道并行专项：16 项通过；扩展前端 23/23 通过。
- `python -m compileall` 和 `git diff --check` 通过。

## 5. 环境边界与验收等级

- 本地：L1，通过 SQLite/测试客户端和真实文件摘要回归。
- 预生产：未完成；尚未对接同版本 MySQL、真实 AICM 发布、TPU-MLIR 和 BM1688 真机证据。
- 生产：未连接、未写入、未提交、未推送、未部署；生产只读审计仍为旧部署。
- 本轮不能宣称 AICM 已被自动调用；当前代码接收 AICM 返回编号并完成平台目录登记。

## 6. 下一依赖

1. 把 AICM 真实上传成功回调与本目录登记放进同一发布 Worker，增加租约/fencing。
2. 在真实 BM1688 设备上验证三精度固定评估证据后才允许生产发布。
