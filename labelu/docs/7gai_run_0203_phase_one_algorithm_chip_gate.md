# RUN-0203 一期算法与 BM1688 执行门禁

- 日期和时区：2026-09-20，Asia/Shanghai。
- 责任角色：R06 模型算法、R04 后端；R07 BM1688 边端、R09 测试协作。
- 工作包：WP0 算法能力边界、WP1 训练/编译/边端写入口、WP5 本地回归。
- 环境级别：本地 L1；未连接生产、真实 TPU-MLIR、BM1688、AICM、S3 或生产数据库。

## 审计结论

算法注册表同时保留分类、分割、姿态和自定义容器定义，这是二期目录能力，不能直接等同于一期可执行能力。此前训练任务服务会根据训练配方写入任意 `job_type`，BM1688 编译配置也允许填写多个芯片平台；只依赖前端隐藏会被直接接口、自动补偿和旧 Worker 绕过。

一期实际执行边界为：

- 训练类型允许 `yolo_detection`，并保留现有车牌 `plate_detection`、`plate_recognition` 历史兼容链；分类、分割、姿态、自定义容器拒绝进入一期训练写链。
- 编译目标只允许 `bm1688`。配置中的 `bm1684x`、`cv186x` 等二期芯片会在创建导出和后台执行前失败关闭。
- 注册表仍可展示二期定义，未删除历史算法定义，也未把页面目录误改成“只有一个算法”。

## 修改文件

- `labelu/internal/common/phase_one_contract.py`
  - 新增一期训练类型和 BM1688 平台校验函数，统一错误码和中文提示。
- `labelu/internal/application/service/training_task.py`
  - 训练任务落库前校验配方生成的 `job_type`。
- `labelu/internal/application/service/yolo_train.py`
  - 直接训练接口解析配方后校验其执行类型，阻断二期配方绕过训练任务入口。
- `labelu/internal/application/service/yolo_bm1688_export.py`
  - 系统自动导出、人工导出、后台恢复/执行均校验训练类型和 BM1688 平台。
- `labelu/internal/application/service/yolo_edge_validation.py`
  - 创建边端验证任务时核对关联训练类型和当前编译平台，避免非一期模型进入设备队列。
- `labelu/tests/internal/common/test_phase_one_algorithm_gate.py`
  - 覆盖一期允许类型、二期类型拒绝、大小写归一化和非 BM1688 平台拒绝。

## 本地验证

- `python -m py_compile labelu/internal/common/phase_one_contract.py labelu/internal/application/service/training_task.py labelu/internal/application/service/yolo_train.py labelu/internal/application/service/yolo_bm1688_export.py labelu/internal/application/service/yolo_edge_validation.py labelu/tests/internal/common/test_phase_one_algorithm_gate.py`：通过。
- `python -m pytest labelu/tests/internal/common/test_phase_one_algorithm_gate.py -q --disable-warnings --tb=short`：`8 passed`。
- `python -m pytest labelu/tests/internal/application/service/test_training_task.py -q --disable-warnings --tb=short`：`16 passed`。
- `python -m pytest labelu/tests/internal/adapter/routers/test_yolo_bm1688_exports.py -q --disable-warnings --tb=short`：`35 passed, 1 skipped`。
- `python -m pytest labelu/tests/internal/adapter/routers/test_yolo_edge_validation.py -q --disable-warnings --tb=short`：`31 passed`。
- `python -m pytest labelu/tests/internal/application/service/test_yolo_bm1688_edge_business_flow.py labelu/tests/internal/application/service/test_bm1688_compile_flow.py -q --disable-warnings --tb=short`：`8 passed`。
- `git diff --check`：通过。

## 并发与失败关闭

门禁位于服务层写入/派发边界，不依赖前端下拉框；自动编译补偿、人工导出、后台恢复和边端创建共用同一校验。校验失败不创建新导出/边端任务；后台已存在的非法记录在执行前抛出错误，由现有失败收尾逻辑关闭，避免继续产生二期制品。

## 未完成和生产阻断

- 本轮未验证真实生产 MySQL 中的算法目录、编译配置和历史记录分布。
- 未验证真实 TPU-MLIR 是否只输出 BM1688 的 FP32/FP16/INT8 三套 BModel，也未在 BM1688 真机加载、推理、P95 和短稳。
- 未验证生产 Worker 重启、20 并发领取、锁等待、AICM 发布和本地模型下载传播。
- 因无真实环境证据，本轮只达到本地 L1；未提交、未推送、未部署。生产放行前必须补齐 R07/R09/R10 的真机、并发和部署后双角色回归。
