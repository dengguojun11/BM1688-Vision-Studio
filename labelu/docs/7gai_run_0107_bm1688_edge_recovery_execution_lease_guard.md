# RUN-0107 BM1688 编译与边端验证恢复执行租约门禁

## 1. 本轮目标

BM1688 编译恢复和边端验证恢复各自有恢复/调度租约，但恢复租约只负责决定“谁可以尝试恢复”，不代表实际编译或真机验证的执行租约。旧恢复线程在作业已经被独立执行器持有时仍可能被创建，边端验证甚至可能在领取执行租约前先做回队处理。本轮把有效 `execution lease` 检查前移到两个恢复入口。

## 2. 实现内容

- `maybe_auto_resume_export()` 读取 BM1688 导出作业后，若存在未过期 `execution_lease_owner`，立即跳过恢复，不解析 ONNX、不领取恢复租约、不创建编译线程。
- `maybe_auto_resume_edge_validation_job()` 读取边端验证作业后，若存在未过期执行租约，立即跳过恢复，不执行陈旧判断、不回队、不启动 SSH 验证线程。
- 两条真正的执行函数仍保留原有数据库 `claim_execution_lease()` 门禁；本轮是恢复入口的前置短路，不替代跨进程条件领取。
- 复用 `execution_lease_is_active()`，避免恢复模块自行解释 owner/过期时间；不新增菜单、用户角色、项目维度、接口、数据库字段或模型制品。

## 3. 验收证据

```text
python -m pytest labelu/tests/internal/application/service/test_yolo_bm1688_export_auto_resume.py labelu/tests/internal/application/service/test_yolo_edge_validation_auto_resume.py -q --disable-warnings --tb=short
```

结果：`41 passed`，零失败；覆盖有效执行租约跳过、过期/无租约兼容恢复、自动计划门禁、边端陈旧作业和既有恢复路径。

`py_compile` 和本轮相关文件 `git diff --check` 通过。

## 4. 并发、性能与失败边界

- 前置判断只读取已加载作业对象，不增加数据库请求；真正执行前仍由 `claim_execution_lease()` 负责跨进程原子领取。
- 恢复租约与执行租约保持职责分离：恢复租约防止多个调度器重复提交，执行租约防止多个编译/边端 Worker 同时产生副作用。
- 本地测试为 Windows/SQLite 和模拟线程；尚未证明同版本 MySQL 锁等待、跨进程恢复接管、真实 BM1688、SSH/SFTP、GPU/设备资源和生产容量。
- 在真实迁移、执行器重启和设备证据完成前，独立编译与边端验证 Worker 继续 `fail-closed`，不能切换生产独立消费路径。

## 5. 角色验收

| 角色 | 本轮职责 | 结果 |
|---|---|---|
| R04 后端 | 两条自动恢复入口前置执行租约门禁 | 本地完成 |
| R03 架构 | 明确恢复租约与执行租约分层，保留跨进程原子领取 | 本地静态确认 |
| R06 模型算法 | 确认有效租约跳过不改变 PT/ONNX/BModel 版本关系 | 本地静态确认；真实工具链未验收 |
| R07 边端 | 确认有效租约下不重复启动 BM1688/SSH 验证 | 本地专项通过；真实设备未验收 |
| R09 测试 | 覆盖编译恢复、边端恢复及有效/失效租约 | `41 passed` |
| R10 运维 | 复核跨进程、重启和设备资源边界 | 本轮未执行生产环境 |

## 6. 交付状态

本轮为本地 L1，未提交、未推送、未部署。下一依赖为同版本 MySQL 双进程恢复接管、systemd 重启、真实 BM1688 编译/边端验证和设备资源释放证据。
