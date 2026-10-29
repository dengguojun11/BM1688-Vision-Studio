# RUN-0319 真实 BM1688 制品与边端验证审计（2026-09-20）

## 1. 本轮目标

按 R06 模型算法、R07 BM1688 边端、R09 测试和 R10 运维职责，使用生产主机只读核对真实 `export_id=226`、训练作业 `383`、边端验证作业 `12688` 的 PT、静态 ONNX、FP32/FP16/INT8 BModel 和真机执行证据。本轮不修改生产数据、不重跑生产任务、不提交、不部署。

## 2. 真实制品与设备证据

生产目录：`/data/labeIU/labeIuMedia/yolo_bm1688_exports/226`。

| 项目 | 只读证据 |
|---|---|
| ONNX | `models/onnx/yolo11n.onnx`，SHA256 `5a3b92a22d443eec9d66ce7d4c347c8fb0d0833aa0da24c92bcd9c221ab040e7` |
| FP32 BModel | `yolov11n_fp32_1b.bmodel`，SHA256 `c0d2422b118cf496e69c466abd8803f40b11de5c9a06c5113ccb8a2581e1c8de` |
| FP16 BModel | `yolov11n_fp16_1b.bmodel`，SHA256 `14e8c73a6b93e27daa9daa1858d4688d1e4f37edae3126aa0c56356bef2d59cb` |
| INT8 BModel | `yolov11n_int8_1b.bmodel`，SHA256 `d1fd2d93912c01660ba2279fbc9fb70902408b236e211270c448b9861c2dca18` |
| 校准集 | `calibration/`，共 128 张；生产制品目录同时有 `calib.zip` |
| 真机 | 主机名 `bm1688`，设备序列号 `9b010059325030333200b2ce56a37a00`，AArch64，Sophon Sail `master(20250306_134147)` |
| 真机报告 | `manual_edge_validation/job-12688/edge_ssh_pull/output/board_report.json`、`bmodel_sop_eval.json`、`summary.json` |

`board_report.json` 明确记录三个 BModel 的 `bmrt_test` 均 `returncode=0`，设备 `chip_id=377922048`；FP32、FP16、INT8 的 `yolov8_bmcv` 推理步骤也均返回 0。该证据证明“BModel 已在 BM1688 上加载并实际执行”，不是仅在服务器打包或模拟。

## 3. 同批评估结果

边端验证包 `pack_signature=2d0ce46c8190976168af6fed79005944f1ef1f2b786c7e668b7dad93a14da088`，50 张图片、50 张有标签图片，类别顺序和数据签名与 PT 同批评估一致。真实同批结果如下：

| 制品 | 图片数 | mAP50 | mAP50-95 | 平均推理耗时 |
|---|---:|---:|---:|---:|
| PT | 50 | 0.9881 | 0.8740 | 未记录 |
| FP32 BModel | 50 | 0.9741 | 0.9012 | 632.7 ms |
| FP16 BModel | 50 | 0.9741 | 0.8983 | 639.2 ms |
| INT8 BModel | 50 | 0.9742 | 0.8957 | 613.1 ms |

当前系统比较报告 `same_batch_pt_bmodel_comparison.json` 的结论为：

- FP32 相对 PT 掉点 `0.0140`；
- FP16 相对 PT 掉点 `0.0140`；
- INT8 相对 PT 掉点 `0.0139`；
- 当前通过线 `≤0.0100`，人工复核上限 `≤0.0200`，三种精度均为 `review`，不是 `pass`；
- INT8 相对 FP32 掉点约 `0.0000`，量化本身没有额外明显损失。

因此，不能因为 `summary.json` 写了“板端验证通过”就直接发布。这里的“板端验证通过”只表示设备加载和推理步骤成功；模型质量门禁仍处于人工复核状态，发布按钮必须继续阻断。

## 4. 固定评估集与性能证据缺口

本次真实记录存在两个评估集口径：

1. 编译目录下 `board_eval/manifest.json` 记录 32 张固定图片；
2. 边端验证作业 `12688` 的 `run_config.json` 和同批 PT/BModel 对比使用 50 张图片；板端 BMCV 实际处理 49 张。

这说明本次硬件执行证据是真实的，但还不能作为“编译、PT、三精度 BModel、真机全部绑定同一冻结评估集”的完整发布证据。下一次正式发布必须由系统从同一个不可变 `DatasetVersion` 生成唯一固定评估包，编译、PT/ONNX 对比和板端三精度验证都只接受该包的 `pack_signature`。

另外，`performance_probe_status=disabled`，目前只有单轮平均阶段耗时，没有 P95、长稳、资源曲线和断网/重启接管证据，不能完成 L3/L4 性能放行。

## 5. 角色验收结论

| 角色 | 结论 |
|---|---|
| R06 模型算法 | PT、ONNX、三精度 BModel 和同批比较报告存在；三精度质量均需人工复核，不能发布 |
| R07 BM1688 边端 | 真实设备身份、三精度 `bmrt_test`、BMCV 推理和 SHA256 证据存在；短稳/P95 未完成 |
| R09 测试 | 真实硬件执行成立；固定集口径不一致和质量门禁 review 必须记录为阻断项 |
| R10 运维 | 只读核验完成；未改生产开关、未重启服务、未部署新版本 |

## 6. 放行状态

- 真实 BM1688 硬件执行：**已证实**；本项达到 L3 的硬件执行子项。
- 模型质量发布门禁：**未通过**，等待人工复核或重新训练/重新编译。
- 同一冻结评估集：**未完成**，需统一 32/50 张口径并重新产生证据。
- P95/短稳/断网/重启接管：**未完成**。
- AICM 发布、本平台下载目录和第三方 S3 回流三方对账：**未完成**。

相关 Worker 和自动发布开关继续失败关闭；本轮没有生产写入、提交、推送或部署。

## 7. 本地记录校验

本轮新增/更新文档通过角色手册和证据完整性专项：

```text
python -m pytest labelu/tests/internal/common/test_7gai_role_execution_playbook.py scripts/tests/test_release_evidence_integrity.py -q --disable-warnings --tb=short
10 passed, 114 warnings

python labelu/scripts/release_gate.py
migration_heads=ze1f2a3b4c5d
security_valid=True
backup_restore_ok=True
release_gate_valid=True
```

上述是本地文档/发布门禁校验，不替代生产部署验收。
