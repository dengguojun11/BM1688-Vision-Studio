# RUN-0287 前端修复后统一本地门禁复核

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

证据文件：`.tmp/7gai-local-gates-current/local-gates-20260920T100532Z.json`。

| 门禁 | 结果 |
|---|---|
| 单一迁移头 | PASSED |
| 空库重复升级 | PASSED |
| 前端外部门禁语义 | PASSED |
| 去项目归属审计 | PASSED |
| 发布证据完整性 | PASSED |
| 预发布探针证据校验 | PASSED |
| Provider 本地契约 | PASSED |
| Agent 本地契约 | PASSED |

汇总：`blocking=0`，命令退出码 `0`。

## 3. 边界

- 这是前端类型修复后的本地回归，不是生产验收。
- 未连接生产、同版本 MySQL、第三方 S3、AICM、TPU-MLIR 或真实 BM1688；未执行迁移、生产写入、提交、推送或部署。
- 真实训练节点、三精度 BModel、真机推理/P95/短稳、AICM/本地目录/S3 三方对账仍由 `WP3-HIL-01` 阻断项承接。

