# RUN-0285 统一本地验收门禁清单

日期：2026-09-20（Asia/Shanghai）  
责任角色：R01 产品、R03 架构、R04 后端、R06 模型算法、R07 BM1688、R08 前端/UI、R09 测试、R10 运维  
工作包：WP0-WP5、OPS

## 1. 执行命令

```text
python scripts/verify_staging_and_hil.py \
  --environment local \
  --run-local \
  --check \
  --evidence-root .tmp/7gai-local-gates-current
```

## 2. 结果

证据文件：`.tmp/7gai-local-gates-current/local-gates-20260920T095428Z.json`。

| 门禁 | 结果 |
|---|---|
| 迁移单头 | PASSED |
| 空库重复升级 | PASSED |
| 前端外部门禁语义 | PASSED |
| 去项目归属审计 | PASSED |
| 发布证据完整性 | PASSED |
| 预发布探针证据校验 | PASSED |
| Provider 本地契约 | PASSED |
| Agent 本地契约 | PASSED |

汇总：`8 PASSED`，`blocking=0`，命令退出码 `0`。

## 3. 解释边界

- 这证明当前工作树的本地契约、代码测试和失败关闭门禁闭合，不证明生产已部署。
- staging/生产 API、两类生产账号、同版本 MySQL 锁等待与 `2/10/20/50` 并发、真实 TPU-MLIR、真实 BM1688、AICM/S3 三方对账仍未验证。
- 未执行迁移、生产写入、SSH 设备操作、提交、推送或部署。

## 4. 下一依赖

先取得真实 BM1688/TPU-MLIR 和授权预生产环境，完成 `WP3-HIL-01`；用户明确“提交代码”后，才能进入备份、迁移、部署和线上复验。

