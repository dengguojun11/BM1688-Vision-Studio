# RUN-0273 本地验收运行手册与统一门禁闭环（2026-09-20）

## 发现

对应主方案条款：15.8（性能、容量和恢复基线）、15.10（测试与验收证据矩阵）、15.11（独立边端验证 Worker）、15.12（去项目化生产验收）。

统一验收脚本的本地门禁 `QA-08-E2E-SKIP-001` 引用了 `docs/系统方案文档/7GAI staging与HIL验收运行手册.md`，但该手册在工作树中不存在，导致本地门禁集合无法完整通过。该问题会影响验收人员理解真实预生产、axe/键盘、BM1688 HIL 和失败关闭边界，不能通过跳过测试规避。

## 修改

- 新增 `docs/系统方案文档/7GAI staging与HIL验收运行手册.md`。
- 手册固定两类账号回放、六个一级菜单、无项目/业务空间、中文居中弹窗、真实 API 与页面核验要求。
- 手册明确 PT -> ONNX -> FP32/FP16/INT8 BModel -> BM1688 真机验证顺序，以及固定评估集、签名、公钥、租约/fencing、断网/重启和外部平台编号对账证据。
- 手册明确 `axe/键盘` 验收和“不能把 mock 前端或 HTTP 200 当成业务通过”。
- 继续保持生产、正式迁移、设备发布和模型回滚边界不变。

## 验证

```text
python -m pytest scripts/tests/test_local_gate_inventory.py -q --disable-warnings --tb=short
3 passed

python scripts/verify_staging_and_hil.py --environment local --evidence-root .tmp/7gai-evidence-0273b --run-local --check --emit-template
blocking=0
8 个 local_windows 门禁全部 PASSED

python scripts/verify_staging_and_hil.py --environment all --evidence-root .tmp/7gai-evidence-0273b --check
blocking=9（全部为未配置预生产/真实硬件/Linux 发布环境的外部前置条件）
```

证据文件：

- `.tmp/7gai-evidence-0273b/local-gates-20260920T062723Z.json`
- `.tmp/7gai-evidence-0273b/local/worktree-20260920T062723Z.json`

每个门禁均记录代码提交摘要、工作树指纹、日志路径和 SHA256；本轮没有连接 staging、生产、MySQL、S3、AICM 或 BM1688。

## 结论

- 本地验收编排从“缺运行手册导致阻断”恢复为 `blocking=0`。
- 全环境检查仍正确报告 9 个外部阻断项；BM1688 HIL 没有工作目录时保持 `SKIPPED_UNAVAILABLE`，没有伪造通过。
- 这只证明本地契约和证据编排完整，不等于预生产、真实 BM1688 或生产通过。
- 继续等待真实 MySQL 沙箱、TPU-MLIR、BM1688、AICM/S3 和生产账号证据；未提交、未推送、未部署。
