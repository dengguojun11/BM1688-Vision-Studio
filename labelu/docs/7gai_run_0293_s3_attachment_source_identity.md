# RUN-0293 第三方 S3 附件来源身份与同名去重（2026-09-20）

## 目标

修复第三方 S3 回流中“同一任务 + 同名文件”被当成本地同一附件的问题。S3 对象现在以桶、对象键、版本号、ETag 和下载后的 SHA-256 保存来源身份；同内容重试复用原附件，同名但来源身份不同的对象使用稳定后缀保存，不能覆盖或复用旧文件。

本轮只处理来源身份、下载复用和附件去重键，不改变任务创建、样本分流、预标注、标签契约和回流状态机规则。

## 实现规则

| 场景 | 处理方式 | 结果 |
|---|---|---|
| 首次导入对象 | 使用原文件名路径，并将 S3 HEAD 身份及下载 SHA-256 写入附件 | 保持现有页面 URL 兼容 |
| 同任务、同名、相同 S3 对象 | 按桶 + 对象键 + 版本 + ETag 查询已有附件；SHA-256 一致时复用 | 不创建重复附件和样本 |
| 同任务、同名、不同对象或不同 ETag | 原文件名已占用时，对本地路径追加来源身份摘要后缀 | 不覆盖旧对象，两个附件可并存 |
| 下载重试 | 已有附件记录 SHA-256 时，先校验本地文件摘要；摘要不符则重新下载 | 不再只按文件大小复用 |
| 历史附件 | 旧记录无来源字段时不强行猜测外部身份；仍可通过旧 S3 数据源路径兼容迁移 | 历史数据不被批量改写 |

真实 S3 默认提供 ETag；如果对象由多段上传产生，ETag 不作为内容哈希，仍以下载后的 SHA-256 作为内容证据。对象版本号存在时与 ETag 一起保存，防止同键对象替换后误复用。

## 代码与数据库变更

- `labelu/internal/domain/models/attachment.py`
  - 增加 `source_bucket`、`source_object_key`、`source_version_id`、`source_etag`、`source_sha256`。
  - 增加任务范围来源身份索引，MySQL 对对象键使用前缀索引，避免超过索引长度限制。
- `labelu/alembic_labelu/versions/zb1c2d3e4f5g_add_s3_attachment_source_identity.py`
  - 为已有 `task_attachment` 增量添加 nullable 字段和索引；降级保持失败关闭。
- `labelu/internal/application/service/experiment_dataset_import.py`
  - S3 HEAD 身份读取、内容摘要计算、来源感知本地路径选择和附件创建/复用。
  - 文件已存在时校验 SHA-256，不再以“文件大小相同”作为充分条件。
- `labelu/tests/internal/adapter/routers/test_experiment_dataset_import.py`
  - 新增同名不同 ETag 产生稳定后缀、同来源身份复用附件的测试。

## 本地验证

```text
python -m pytest labelu/tests/internal/adapter/routers/test_experiment_dataset_import.py -q --disable-warnings --tb=short
25 passed

python -m pytest labelu/tests/alembic/test_experiment_import_worker_migration.py labelu/tests/alembic/test_system_label_contracts_migration.py -q --disable-warnings --tb=short
3 passed

python -m pytest labelu/tests/internal/common/test_migration_release_gate.py labelu/tests/internal/common/test_run_migrate.py -q --disable-warnings --tb=short
17 passed

python -m compileall -q labelu/internal/domain/models/attachment.py labelu/internal/application/service/experiment_dataset_import.py labelu/alembic_labelu/versions/zb1c2d3e4f5g_add_s3_attachment_source_identity.py
通过
```

## 并发与性能边界

- 来源查询按任务、桶、对象键和删除状态过滤；MySQL 对长对象键使用前缀索引，完整 ETag/版本仍在条件过滤中校验。
- 不改变现有 Worker 租约和聚合组 fencing；同一对象的并发导入仍由任务/样本幂等逻辑收敛。
- 本轮未完成真实 MySQL 锁等待、两个 Worker 同名对象并发下载、S3 版本替换和大文件 SHA-256 吞吐测试。

## 当前等级与下一步

当前为本地 L1：假 S3/SQLite 和迁移门禁通过；未提交、未推送、未部署。提交授权后需在同版本 MySQL、真实 S3/MinIO 上验证：

1. 同名不同内容最终产生两个附件，两个 URL 均可读取；
2. 同内容重试只保留一个附件和一个样本关联；
3. ETag 缺失或多段上传对象仍能通过 SHA-256 防止错误复用；
4. 对象替换、Worker 重启和 2/10/20 并发下没有覆盖文件或重复样本；
5. 迁移备份恢复、索引长度和对象下载吞吐满足生产基线。

