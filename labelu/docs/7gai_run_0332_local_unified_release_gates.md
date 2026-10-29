# RUN-0332 本地统一放行门禁

- 日期：2026-09-21
- 环境：本地 Windows、进程级 SQLite（`.tmp/labelu-dev.sqlite`）
- 目标：在不连接生产、不提交代码的前提下，统一核验迁移、前端外部门禁、去项目化、发布证据、Provider、Agent/OTA、工作树和证据完整性。
- 执行入口：`python scripts/verify_staging_and_hil.py --environment local --run-local --check --evidence-root .tmp/evidence/run0332`

## 结果

统一门禁结果为 `blocking: 0`。证据汇总文件为：

`.tmp/evidence/run0332/local-gates-20260921T020952Z.json`

通过项包括：

- Alembic 单一迁移头和空库重复升级；
- 前端外部门禁语义；
- 去项目化审计；
- 发布证据完整性；
- Provider 本地契约；
- Agent/OTA 本地契约；
- 工作树指纹和证据完整性。

## 边界

本轮是本地放行门禁，不代表真实 MySQL、GPU、TPU-MLIR、BM1688 真机、AICM、第三方 S3 或生产反向代理已验收。未提交、未推送、未部署、未重启生产。
