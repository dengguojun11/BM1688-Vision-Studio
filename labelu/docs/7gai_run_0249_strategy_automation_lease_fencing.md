# RUN-0249 进化中心自动推进租约写入栅栏

- 日期：2026-09-20（Asia/Shanghai）
- 责任角色：R01 产品、R03 架构、R04 后端、R05 外部平台、R06 模型算法、R09 测试、R10 运维
- 工作包：WP4（回流与外部平台）、WP5（测试与生产放行）
- 涉及菜单/按钮：进化中心“查看回流批次”“重试失败阶段”“进入重新标注”；训练中心“查看训练作业”
- 验收等级：本地 L1；未连接生产

## 1. 业务链和本轮缺口

一期正常链路保持为：第三方 S3 问题包进入实验数据集导入 Worker，完成包校验、图片去重、AICM `model_set_id` 对齐和任务复用/创建；标注确认满足条件后，进化中心自动推进模型能力任务并触发训练。正常数据由系统自动处理，标注人员按任务权限重新标注，管理人员只处理模型冲突、契约异常、失败重试和少量抽查。系统不增加项目/业务空间，也不增加第三种登录角色。

已有自动推进使用数据库租约，但同一后端进程的多个线程共用一个执行者编号，仍可重复领取同一任务；租约过期被其他进程接管后，旧进程的自动化快照写入也没有再次校验当前持有者。这会导致旧结果覆盖新状态，进而干扰训练、编译、边端验证和发布阶段判断。

## 2. 实现范围

| 层级 | 文件 | 实现 |
|---|---|---|
| 持久化 | `labelu/internal/adapter/persistence/crud_strategy_task_integration.py` | 活跃租约无论执行者名称是否相同都禁止重复领取；新增按外部任务编号、租约持有者和未过期时间的条件更新 |
| 服务 | `labelu/internal/application/service/strategy_evolution.py` | 每次自动推进生成唯一执行者编号；自动化快照必须由当前未过期租约写入；失租后失败关闭，不继续发送终态回调，不释放新执行者租约 |
| 测试 | `labelu/tests/internal/application/service/test_strategy_evolution_auto_advance.py` | 覆盖同进程重复领取、租约过期接管、旧执行者迟到写入和自然过期写入拒绝 |

本轮不修改数据库表和 Alembic，不新增页面、按钮、项目字段、设备发布、模型回滚或人员角色。手工首次接受第三方任务仍可复用无租约快照写入；只有后台自动推进路径启用租约条件写入。

## 3. 并发、幂等和性能

- 同一任务的每次推进使用“进程编号 + 随机执行编号”，同一进程内并发调用也属于不同执行代次。
- 活跃租约不能由相同或不同执行者重复领取；过期后允许新执行者接管。
- 自动化快照使用单条条件更新，只在外部任务唯一键命中后校验租约持有者和过期时间，不扫描图片或训练表，不增加全局锁。
- 旧执行者失租后，状态、派生训练编号和终态回调均不再由该次推进继续写入；租约释放仍按执行者编号匹配，不能清除新执行者租约。
- S3 包级幂等、图片内容去重、训练作业/编译/边端 Worker 的既有幂等与 fencing 继续复用，不在本轮另建一套状态机。

## 4. 权限与页面规则

- 标注人员和管理人员都可按既有任务权限查看进化任务及进入重新标注；管理人员处理全局异常和规则。
- “重试失败阶段”只重新进入同一外部任务状态机，不创建项目，也不复制一个新的业务空间。
- 本轮没有前端交互变化；后续若调整相关详情或确认操作，继续使用中文居中弹窗，不使用抽屉。

## 5. 本地证据

```text
python -m pytest labelu/tests/internal/application/service/test_strategy_evolution_auto_advance.py -q --disable-warnings --tb=short
33 passed, 113 warnings

python -m pytest labelu/tests/internal/adapter/routers/test_strategy_evolution.py labelu/tests/internal/application/service/test_strategy_evolution_auto_advance.py labelu/tests/internal/application/service/test_strategy_evolution_no_project_scope.py labelu/tests/internal/application/service/test_strategy_workflow_bridge.py -q --disable-warnings --tb=short
75 passed, 113 warnings
```

隔离测试对象仅使用测试数据库中的 `strategy-task-same-process`、`strategy-task-late-write` 和 `strategy-task-expired-write`，没有生产请求编号、训练作业编号或模型编号。

## 6. 生产边界与遗留风险

- 未连接生产 MySQL、S3、AICM、GPU、TPU-MLIR、BM1688 或 systemd，未接收真实问题包，未启动真实训练。
- 未执行 MySQL `2/10/20/50` 并发、锁等待、租约跨进程接管和进程崩溃恢复；SQLite 单元测试不能替代这些证据。
- 本轮阻止自动化快照和终态回调的迟到写入。训练创建、编译、边端验证和 AICM 发布等下游副作用仍必须依赖各自业务幂等键与 owner/token；真实多 Worker 联合演练未完成。
- 外部终态回调继续使用稳定事件编号要求接收方幂等；网络请求发出后瞬间失租无法撤回已送达的 HTTP 请求。
- 未提交、未推送、未部署；生产自动训练和相关 Worker 的放行状态不因本地 L1 自动改变。

## 7. 结论

自动回流到训练触发的后台推进补齐了“同进程不重复领取、过期执行者不能迟到覆盖”的本地代码门禁。该结果只证明本地状态写入栅栏有效，不能宣称真实 S3 回流、训练或 BM1688 闭环已在生产完成。
