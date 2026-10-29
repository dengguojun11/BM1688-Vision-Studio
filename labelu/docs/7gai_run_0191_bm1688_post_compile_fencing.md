# RUN-0191 BM1688 编译完成后回调执行期 fencing（2026-09-20，本地 L1）

- 责任角色：R03 架构、R04 后端、R07 BM1688 边端、R09 测试；R06 模型算法协作。
- 工作包：WP3 BM1688/边端执行、WP5 并发与生产放行。
- 目标：防止 BM1688 编译 Worker 在执行租约失效或被接管后，继续创建边端验证任务、生成服务器评估文件或覆盖质量报告。

## 本轮修改

- `yolo_bm1688_export._maybe_run_edge_ssh_validation_sync` 增加可选执行监视器；创建/查询统一边端验证任务前后复核当前 fencing token，`ExecutionFencingLost` 不再被吞掉。
- `yolo_bm1688_export._generate_quality_report_sync` 增加执行监视器；质量报告持久化前使用当前 token 条件写入，旧 Worker 不能覆盖新 Worker 的报告。
- `yolo_sop_model_evaluation.run_server_sop_evaluations_for_export` 在评估目录创建、PT/ONNX 评估前后和摘要落盘前复核执行租约。
- `bm1688_quality_report.persist_quality_report_for_export` 支持执行监视器；成功报告和失败报告均通过条件更新写入，保留无监视器的历史兼容调用。
- 复编译路径和复合车牌识别 BM1688 路径均传递同一执行监视器；没有执行租约的历史数据库继续走兼容路径。

## 行为边界

1. 编译状态已经落库后，任何后处理回调都必须再次检查当前执行租约。
2. 租约丢失时抛出 `ExecutionFencingLost`，上层停止后续回调，不把旧 Worker 标记为新的失败或完成。
3. 质量报告的数据库写入使用 fencing 条件更新；报告生成过程中已产生的本地临时文件不作为有效平台状态，后续由新 Worker 重建或覆盖。
4. 本轮没有改变 SSH/Board Agent 的设备执行协议、模型发布下载开关或生产回滚边界。

## 本地证据

```text
python -m pytest labelu/tests/internal/application/service/test_yolo_bm1688_export_auto_resume.py labelu/tests/internal/application/service/test_plate_recognition_bm1688.py -q --disable-warnings --tb=short
47 passed

python -m pytest labelu/tests/internal/common/test_bm1688_quality_refresh.py labelu/tests/internal/application/service/test_worker_execution_fencing_contract.py labelu/tests/internal/application/service/test_bm1688_worker_runtime.py labelu/tests/internal/application/service/test_yolo_bm1688_edge_business_flow.py -q --disable-warnings --tb=short
26 passed

python -m py_compile labelu/internal/application/service/yolo_bm1688_export.py labelu/internal/application/service/plate_recognition_bm1688.py labelu/internal/common/bm1688_quality_report.py labelu/internal/application/service/yolo_sop_model_evaluation.py
通过
```

新增专项断言覆盖：

- 失租时不调用统一边端验证任务创建入口；
- 失租时不进入质量报告持久化；
- 失租时不创建服务器评估目录或运行评估脚本；
- 复合车牌导出仍保持原有回调参数和成功路径。

## 生产边界与下一步

- 未连接生产 MySQL、TPU-MLIR、BM1688 真机、AICM、S3 或 systemd；未提交、未推送、未部署。
- 本轮为本地 L1，不能宣称生产并发验收通过。
- 进入 L2/L3 前仍需在同版本 MySQL 执行双 Worker 失租接管、真实 BM1688 长时间编译/重启、边端任务创建对账和质量报告版本对账。
