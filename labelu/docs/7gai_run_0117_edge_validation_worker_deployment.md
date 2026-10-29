# RUN-0117 独立边端验证 Worker 部署化接入

## 1. 本轮目标

把 RUN-0114 的边端验证 Worker 接入部署生命周期，使生产部署具备独立 systemd 单元、默认关闭、显式启用、启停/重启/停止/`reset-clean` 管理和部署检查清单。Web 进程只负责设备分配；独立 Worker 负责领取任务、SSH、样本包上传、BM1688 推理和结果回传。

本轮仍属于本地 L1 交付，不连接生产，不执行真实 systemd、SSH、TPU-MLIR、BM1688、S3 或 AICM。

## 2. 角色与工作包

| 角色 | 工作包 | 本轮交付边界 |
|---|---|---|
| R07 边端算法/运行时 | WP3 | 复用 `labelu.scripts.edge_validation_worker`，不复制边端状态机；保持设备分配、execution lease、fencing 和三精度验证入口不变 |
| R10 运维 | WP3/WP5 | 增加 `labelu-edge-validation-worker.service`，接入部署、启停、重启、停止和清理生命周期；增加资源与权限隔离配置 |
| R04 后端 | WP3 | Web 开关启用时关闭进程内边端执行，确保独立 Worker 成为唯一消费路径；默认关闭时保留迁移兼容路径 |
| R09 测试 | WP5 | 增加部署单元契约测试，检查开关、入口、隔离项、生命周期和数值参数校验 |

## 3. 实际修改

- `deploy.sh`
  - 声明 `labelu-edge-validation-worker.service`；
  - 新增 `LABELU_ENABLE_EDGE_VALIDATION_WORKER=0` 默认关闭；
  - 新增独立 Worker 轮询间隔和批量上限；
  - 生成 systemd 单元并设置 `NoNewPrivileges`、`PrivateTmp`、`ProtectSystem`、`ProtectHome` 和受限可写目录；
  - `start`、`restart`、`stop`、`reset-clean` 和部署检查清单均接入该单元；
  - 启用独立 Worker 时，Web 单元写入 `YOLO_EDGE_VALIDATION_DEDICATED_WORKER_ENABLED=false`，避免双路径同时执行；
  - 只从运行时 `.env` 读取部署控制项，不 source 全量生产密钥。
- `.env.example`
  - 增加独立 Worker 开关、轮询间隔和批量上限说明；
  - 明确启用前必须具备真实设备、正式迁移和工具链证据。
- `scripts/tests/test_deploy_edge_validation_worker_units.sh`
  - 增加部署单元静态契约检查和数值参数失败关闭检查。

## 4. 验证结果

已完成：

- Python 边端 Worker 入口、运行时和相关专项回归仍保持 RUN-0114/RUN-0116 结果；
- 已静态核对部署脚本包含独立单元、入口、Web 侧关闭、生命周期、检查清单和 systemd 隔离项；
- `git diff --check` 通过。

未完成且明确记录：

- 当前 Windows 只有 `C:\Windows\System32\bash.exe` 的 WSL 启动器，实际执行返回 `WSL ERROR: execvpe /bin/bash failed`；没有可用 Git Bash 或 WSL Linux，因此 `bash -n deploy.sh`、`test_deploy_edge_validation_worker_units.sh` 不能在本机执行；
- 未在真实 Ubuntu/systemd 主机生成并加载单元；
- 未连接真实 MySQL、BM1688、SSH 设备和生产数据。

因此本轮不能宣称 Bash 语法、systemd 启动或生产边端验证通过。

## 5. 并发与性能边界

- Web 调度器、独立边端 Worker 和 BM1688 编译 Worker 仍通过不同执行租约隔离；本轮没有新增数据库状态机；
- 默认 `batch-limit=1`，避免单块 BM1688 设备被同一 Worker 并行占用；
- 轮询和批量参数只作为部署配置，不能替代 MySQL 原子领取、execution lease 续期和旧 token 拒绝；
- systemd 资源隔离只限制进程边界，不能证明真实设备吞吐、P95、锁等待或板卡资源释放。

## 6. 生产放行条件

1. 在与生产同版本的 Ubuntu/systemd 上执行 `bash -n deploy.sh` 和本脚本契约测试；
2. 生成单元后执行 `systemctl daemon-reload`、启停、重启和异常退出恢复，确认 Web 进程不再启动边端线程；
3. 在同版本 MySQL 验证双进程领取、execution lease 接管、旧 fencing token 拒绝、锁等待和 20 并发唯一领取；
4. 在真实 BM1688 上验证样本包上传、FP32/FP16/INT8 BModel 加载、有效推理数、错误率、精度、P95、短稳和网络中断恢复；
5. 以上证据完成前，生产配置保持 `LABELU_ENABLE_EDGE_VALIDATION_WORKER=0`，不得进行独立 Worker 灰度。

## 7. 交付状态

- 本地代码：已修改；
- 本地可执行验证：Python/静态检查范围内完成；Bash 测试受环境阻塞；
- 预生产/生产：未执行；
- 提交、推送、部署：均未执行，等待用户明确“提交代码”。

结论：独立边端 Worker 的部署化结构达到本地 L1；Linux/systemd、MySQL 并发、真实设备和生产闭环仍未通过，平台整体不得超过 L1。
