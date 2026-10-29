# RUN-0193 BM1688 发布门禁 Fail-Closed 收口

> 日期：2026-09-20  
> 责任角色：R06 模型算法、R07 BM1688 边端、R04 后端、R09 测试  
> 范围：本地代码与测试；未连接生产、未提交、未推送、未部署。

## 结论

旧 AICM 手工发布链曾允许用 `manual` 绕过边端完成状态和 PT/BModel 质量比较。本轮已收口：手工触发与自动触发共用边端证据、固定评估集、ModelManifest、Profile 和精度门禁；缺任一证据直接失败关闭。手工触发不再把 `allow_quality_risk` 变成发布资格，风险模型只能停在复核/不可发布状态。

## 改动

1. `yolo_aicm_publish.py`
   - 手工发布必须有 `COMPLETED` 的边端验证，并重新评估设备、模型、执行世代、三精度、固定评估集、精度、P95、错误率、短稳和 fencing 证据。
   - 固定评估集必须包含可核验的有标注图片；缺失标注、Profile、规则或制品清单时阻断。
   - `allow_quality_risk` 仅保留兼容字段，不能使 `review`/`missing`/`exceeded` 选项可发布。
2. `yolo_bm1688_export.py`
   - 保留 `is_bmodel_artifact_ready` 作为历史/下游的轻量制品状态。
   - 新增 `is_bmodel_release_ready`：通用检测发布必须有 FP32、FP16、INT8 且导出状态为 `COMPLETED`；ONNX_READY、单 BModel 均不可发布。车牌专用运行契约可显式要求 FP16。
3. `edge_board_validate_runner.py`
   - `labeled_image_count == 0` 从 `not_applicable` 改为失败原因 `no_labeled_images`。
   - `not_applicable` 不再被转换为整体 `ok`。

## 本地验证

- BM1688 边端 Runner、导出续跑、AICM 发布门禁、模型发布前端和相关辅助门禁回归：`193 passed`。
- Python 编译：`py_compile` 通过。
- `git diff --check`：通过（仅有工作区既有 CRLF 警告，无 whitespace error）。

## 未覆盖与后续放行条件

- 尚未连接真实 TPU-MLIR、BM1688 板卡、systemd Worker、生产 MySQL、S3 或 AICM。
- 尚未用真实生产模型完成三精度编译、固定有标注验收集、真机短稳和 AICM 三方对账。
- 仍需在生产部署后验证旧历史记录、真实板端报告和发布页面；本 RUN 不代表生产已验收。
