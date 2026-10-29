# RUN-0313 本地统一验收门禁与证据指纹（2026-09-20）

## 1. 目标和范围

由 R03 架构、R06 模型算法、R07 BM1688 边端、R08 前端、R09 测试和 R10 运维执行统一本地验收脚本，确认当前工作树的迁移、前端门禁、去项目化审计、Provider 契约、证据完整性和边端协议测试可重复通过。

本轮只执行 `local_windows` 门禁，不连接预生产、生产、GPU 节点、AICM、S3、TPU-MLIR 或 BM1688，不执行迁移、部署和 Worker 启动。

## 2. 执行命令和证据

```text
python scripts/verify_staging_and_hil.py --environment local --evidence-root .tmp/evidence/run0313 --run-local --check
```

汇总证据：

```text
.tmp/evidence/run0313/local-gates-20260920T143749Z.json
```

工作树指纹：

```text
git_revision=146610b38f3a221ad8214d5cacbe73f4b2c03025
dirty=true
files=1161
fingerprint_sha256=7bfe5f4489fe76e66ced2362fa0214090a5a8e832e409d60deb35efc72f6
```

工作树为脏状态是当前协作开发事实，不代表可以提交或部署；证据文件已记录 revision、变更文件摘要和每个门禁日志 SHA256。

## 3. 门禁结果

| 门禁 | 责任角色 | 结果 |
|---|---|---|
| 迁移唯一 head | R03/R09 | `PASSED` |
| 空库重复升级 | R03/R09/R10 | `PASSED` |
| 前端外部门禁语义 | R08/R09 | `PASSED` |
| 去项目化归属审计演练 | R03/R04/R09 | `PASSED` |
| 发布证据完整性 | R03/R09/R10 | `PASSED` |
| 预生产探针证据校验契约 | R09/R10 | `PASSED` |
| Provider 预标注契约 | R04/R06/R09 | `PASSED` |
| 板端协议/OTA 契约 | R07/R09 | `PASSED` |

汇总：**8 项通过，blocking=0，脚本退出码 0**。

## 4. 验收解释

- 本轮把本地可验证的角色交付重新跑了一遍，证明当前代码和验收脚本没有因后续改动而失效。
- 本轮不包含真实 GPU 训练、真实 TPU-MLIR 编译、三精度 BModel 生成、BM1688 加载/推理、MySQL 并发、AICM 发布、S3 回流或生产浏览器，因此验收等级仍为 **L1 本地**。
- `dirty=true` 和 1161 个工作树文件不会被当作发布版本；只有用户明确“提交代码”后，才进入提交、推送、备份、迁移、预生产和生产验证流程。
- 所有生产 Worker 和自动训练/边端开关继续失败关闭。

## 5. 遗留项和下一依赖

1. R10 提供可达的同版本 MySQL、GPU/TPU-MLIR 节点、BM1688 设备、systemd 和生产只读/写入窗口。
2. R06/R07 在真实设备上产出 PT、静态 ONNX、FP32/FP16/INT8 BModel、固定评估集和三精度真机证据。
3. R05/R10 完成真实 AICM 对账、本平台下载目录、第三方 S3 包、模型 ID 和问题图片回流对账。
4. R08/R09 在隔离环境执行两类账号、桌面/窄屏和真实 API 浏览器验收。

## 6. 状态

- 代码变更：本轮无业务代码变更，仅生成 `.tmp` 证据。
- 数据库/生产：无写入、无迁移、无部署。
- 本轮等级：**L1 本地**。
- 提交/部署：不允许，等待真实环境证据和用户明确“提交代码”。
