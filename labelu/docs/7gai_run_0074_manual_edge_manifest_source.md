# RUN-0074 手工边端验证清单来源收口

## 1. 范围

- 日期：2026-09-18，Asia/Shanghai。
- 目标：让手工 BM1688 边端执行、同批次评估和结果维度使用同一份实际上传样本包清单。
- 范围：`_run_manual_job_sync` 的清单读取和业务流专项测试；不连接生产、不提交、不部署。

## 2. 问题与决定

手工任务构建函数已经把签名包清单复制到 `staging/manifest.json`，但执行函数之前固定从任务根目录读取 `manifest.json`。当旧任务根目录残留历史清单时，边端上传的是新样本包，服务端却可能按旧类别或旧样本范围解释结果，造成评估证据错配。

执行时按以下优先级读取：

1. 本次要上传的 `staging/manifest.json`；
2. 已提交的 `result_json.pack.manifest_path`；
3. 历史任务根目录 `manifest.json`，仅用于老任务兼容。

没有有效清单时，仍受 `READY` 门禁约束，不连接 SSH、不上传、不执行设备副作用。

## 3. 菜单/接口对应关系

- 模型中心 -> 边端验证：调用手工执行链，上传包和服务端评估都读取 staging 清单。
- 训练中心 -> 训练产物 -> 边端验证：legacy 任务先生成不可变签名包，再走同一执行链。
- 边端回传结果：按 staging 清单中的类别契约、样本数量和标注数量做校验和展示。

## 4. 并发与失败处理

- 不新增数据库字段、接口或锁；沿用任务执行租约、fencing 和 `READY` 条件门禁。
- staging 清单缺失时只走兼容回退；所有路径都无法读取有效清单时，任务不会进入 SSH。
- 旧任务根目录清单不会覆盖新 staging 清单；历史任务仍可通过兼容路径运行。

## 5. 本轮实现

- 修改 `labelu/internal/application/service/yolo_edge_validation.py`：优先读取实际 staging 清单。
- 修改 `labelu/tests/internal/application/service/test_yolo_bm1688_edge_business_flow.py`：加入“旧根目录清单 + 新签名包清单”回归场景，断言最终类别范围来自新包。

## 6. 验证与未完成项

- 手工业务流：`1 passed`。
- 联合专项：`71 passed`，覆盖样本包、调度、自动训练、边端路由和 BM1688 边端业务流。
- `py_compile` 与 `git diff --check`：本轮已执行，无错误。
- 仅本地隔离库和模拟 SSH 边界；真实 BM1688、TPU-MLIR、生产 MySQL、S3/AICM、浏览器真机和 20 并发未验证。
- 本轮未提交、未推送、未部署；等待用户明确授权“提交代码”。
