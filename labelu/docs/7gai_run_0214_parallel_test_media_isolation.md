# RUN-0214 并行测试媒体目录隔离（2026-09-20）

## 责任角色

- R09 测试：定位附件上传并发回归的共享媒体目录问题，补并行验证。
- R03 架构、R04 后端：确认只调整测试夹具，不改变生产存储后端、对象键或上传事务。
- R10 运维协作：将并发测试环境的数据库目录和媒体目录都纳入进程隔离约定。

## 发现

测试数据库已经按进程生成独立文件，但 `MEDIA_ROOT` 仍固定为
`.pytest_labelu_data/media`。多个测试进程或并行智能体上传同一个任务文件名时，
一个进程的清理夹具可能删除另一个进程刚写入的附件，造成“服务已记录 File saved，
测试却找不到文件”的假失败。这不是生产上传链路的业务错误，而是测试环境边界不完整。

## 处理

- 在 `labelu/tests/conftest.py` 中按当前进程设置
  `.pytest_labelu_data/media-{pid}` 作为 `MEDIA_ROOT`。
- 保留 `DATABASE_URL` 的进程隔离；生产代码的 `STORAGE_BACKEND`、上传对象键、
  S3 配置和清理语义均未修改。
- 通过两份同时运行的附件路由套件验证不同进程不会互删上传文件。

## 本地验证

```text
python -m pytest labelu/tests/internal/adapter/routers/test_attachment.py \
  -q --disable-warnings --tb=short
20 passed

# 两个相同命令并行执行
进程 A：20 passed
进程 B：20 passed
```

## 放行边界

- 该变更仅提升本地/持续集成并发测试可靠性，不等同于生产多副本上传压力验收。
- 生产仍需单独验证本地存储和 S3 存储的对象键幂等、同名并发上传、清理任务与磁盘/对象存储容量。
- 未连接生产、未提交、未推送、未部署。
