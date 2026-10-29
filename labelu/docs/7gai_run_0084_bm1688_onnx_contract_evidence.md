# RUN-0084 BM1688 制品清单补齐实际 ONNX 导出契约（本地 L1）

- 日期和时区：2026-09-18，Asia/Shanghai。
- 方案章节与需求编号：主方案第9章 BM1688 编译与真机验证；证据契约 `BM1688-EVIDENCE-ONNX-01`。
- 工作包/角色：WP3 模型编译与边端；R06 模型算法主责，R07 边端、R04 后端、R09 测试协作。
- 本轮目标：让 PT→ONNX→FP32/FP16/INT8 BModel 的制品清单记录实际 ONNX 导出参数，而不只记录可变的配置文件。
- 实现内容：`_persist_artifact_manifest()` 在生成制品清单时保存 `onnx_input_imgsz`、`onnx_opset`、`onnx_dynamic`、`onnx_simplify` 和 `onnx_export_backend`；这些字段进入稳定 `execution_signature` 的编译配置，参数变化会导致新的可追溯签名。
- 数据/API/页面影响：不新增菜单、按钮、用户权限或外部接口；模型中心详情可继续读取原有清单 JSON，普通页面不展示技术字段，审计详情可读取实际导出契约。
- 并发、失败和重试：清单仍通过现有边端/编译执行租约条件写入；参数只在制品证据生成时写入，不改变任务领取或发布状态机；清单写入失败继续按既有失败关闭处理。
- 测试命令及结果：
  - `python -m pytest labelu/tests/internal/common/test_model_asset_manifest.py labelu/tests/internal/application/service/test_bm1688_compile_flow.py -q --disable-warnings --tb=short`：`10 passed`。
  - `python -m py_compile labelu/internal/application/service/yolo_bm1688_export.py labelu/internal/common/model_asset_manifest.py`：通过。
  - `git diff --check`：通过。
- 本地、预生产、生产证据：仅本地清单构造、模拟编译测试；未连接真实 TPU-MLIR、BM1688、BMRuntime/BMCV、固件、生产数据库或 AICM。
- 风险与下一依赖：当前仍需在真实编译节点确认 Sophon 导出脚本的实际 opset 与清单一致，并记录 TPU-MLIR、BMRuntime、BMCV、固件/Docker 摘要、三种 BModel 同一封存评估集结果、INT8 校准覆盖和真机 P95/短稳数据。
- commit/push/deploy 状态：未提交、未推送、未部署；等待用户明确说“提交代码”。
- L0/L1/L2/L3/L4 结论：制品参数可追溯达到本地 L1；真实工具链、三精度同批验证、BM1688 真机和生产放行仍未完成。

