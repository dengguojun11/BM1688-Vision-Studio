# RUN-0125 TPU-MLIR 编译链与制品证据门禁

## 1. 本轮定位

本轮由 R06（模型算法）和 R07（BM1688 边端）审计 YOLO11 的实际链路：

`训练完成的 PT -> Sophon 静态 ONNX -> TPU-MLIR 中间文件 -> FP32/FP16/INT8 BModel -> 固定评估集与 BM1688 真机验证 -> 发布`。

本轮只做本地代码和测试，没有调用真实 TPU-MLIR、没有连接 BM1688、没有改生产数据库，也没有把模拟文件写成真机证据。

## 2. 发现的实际问题

1. `labelu/internal/common/bm1688_sophon_onnx.py` 的 Sophon 导出实际使用 `opset=17`、`dynamic=False`、`batch=1`。
2. `yolo_bm1688_export._persist_artifact_manifest()` 原来无条件记录 `onnx_opset=12`。这会让制品清单与实际 ONNX 输入不一致，影响后续复核和稳定执行签名。
3. 编译目录虽然已有三精度文件检查，但缺少一个集中、可复用的本地证据契约，无法统一表达“缺校准集、缺工具链版本、缺固定评估集时不得进入发布”的原因。
4. `compiler_version` 目前由编译执行上下文提供；本地契约要求它必须存在，但不伪造具体 TPU-MLIR 版本号。真实版本仍须在编译节点采集。

## 3. 本轮改动

### 3.1 统一 ONNX 导出契约

新增 `build_onnx_export_contract()`，由制品清单复用实际导出分支：

| 导出分支 | 后端标识 | opset | dynamic | batch | simplify |
|---|---|---:|---|---:|---|
| Sophon 导出 | `sophon` | 17 | `false` | 1 | `true` |
| Ultralytics 兼容导出 | `ultralytics` | 12 | `false` | 1 | `true` |

一期 BM1688 YOLO11 主链使用第一行；第二行只保留兼容分支，不得把它写成 Sophon 生产证据。

### 3.2 新增本地编译证据契约

新增 `evaluate_bm1688_compile_contract()`，统一检查：

- ONNX 文件存在；
- Sophon 导出契约为 opset 至少 17、静态 batch=1；
- 工具链版本字段存在；
- `full` 模式每个平台同时存在 FP32、FP16、INT8 三个 BModel；单精度模式只检查被选择的精度；
- INT8/full 必须有非空图片校准集；
- 制品清单存在稳定 `execution_signature`；
- 固定评估集证据存在。缺失时返回明确原因 `fixed_eval_missing`，不推断为边端通过。

该函数是本地门禁和测试契约，不执行编译，不替代真实 BM1688 验证。真实编译仍由受控 TPU-MLIR 节点执行，真实设备只负责加载和推理，不在板端临时编译。

## 4. 并发和失败关闭

- 该门禁是只读目录检查，不创建或覆盖 BModel，不增加数据库锁竞争。
- 三精度缺任意一种、校准集为空、工具链版本不可确认、固定评估集缺失时，结果为“阻断”；调用方应保持 `ONNX_READY`/待复核，不得显示发布通过。
- 编译完成时固定评估集尚未产生属于正常中间态；只有发布前存在 `COMPLETED` 且有实际结果状态的边端验证时，才会移除这一项等待原因。边端报告仍须通过现有质量对比门禁，不能仅凭进程完成发布。
- 这轮不改变已有执行租约、fencing、独立 Worker 和边端设备租约逻辑；真实 Worker 仍需在正式迁移和设备证据完成后再开启。

## 5. 本地验证

执行：

```text
python -m pytest labelu/tests/internal/common/test_bm1688_compile_contract.py labelu/tests/internal/common/test_model_asset_manifest.py -q
```

结果：`9 passed`。

补充回归：BM1688 编译流与自动编译 `8 passed`；质量评估 `26 passed`；发布范围与质量推荐 `13 passed`；相关 Python 文件 `compileall` 通过。

覆盖：Sophon opset17 契约、三精度完整、缺 INT8、缺校准集、缺工具链版本、缺固定评估集失败关闭，以及现有制品清单摘要回归。

## 6. 尚未完成的真实证据

- 同版本 TPU-MLIR 安装包、`model_transform.py/model_deploy.py` 版本和芯片内核库版本；
- 真实 PT 导出 ONNX、TPU-MLIR 生成三种 BModel；
- 同一封存评估集上的 PT/ONNX/FP32/FP16/INT8 对比；
- BM1688 真机加载、有效推理数、精度、P50/P95/P99、错误率和短稳；
- 真实 MySQL、独立 Worker、systemd、生产 S3/AICM 和生产部署验收。

因此本轮等级为本地 L1，不能写成生产完成或真实边端通过。

## 7. 生产只读复核

执行命令：

```text
python scripts/staging_acceptance.py --base-url http://113.141.72.175:10000 --username <生产只读账号> --password <生产只读密码> --evidence .tmp/evidence/run0125-production-readonly.json
```

证据文件：`.tmp/evidence/run0125-production-readonly.json`。

结果：健康、就绪、版本、登录、任务、训练作业、已发布模型目录、BM1688 编译作业、边端验证作业和第三方 S3 配置查询通过；规范 OpenAPI 路由门禁失败，生产仍缺少 `/api/v1/datasets`、`/api/v1/algorithms`、`/api/v1/model-versions`、`/api/v1/workspace/summary`，并继续暴露 `/api/v1/edge-device-jobs` 旧控制面，相关接口查询返回 404。该证据只读且没有写入生产数据，说明 RUN-0124 代码尚未提交、部署，不能把本轮本地 L1 写成生产完成。
