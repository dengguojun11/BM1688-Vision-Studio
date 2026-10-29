# BM1688 独立 Worker 第 08 段迁移验收清单

## 1. 审计结论

独立编译 Worker 的数据库并发分为两层，不能共用同一组字段：

1. `queue_lease_owner`、`queue_lease_expires_at`负责扫描后到开始执行前的短时领取。
2. `execution_lease_owner`、`execution_lease_expires_at`、`execution_fencing_token`
   负责真实编译或边端验证期间的续租、失联接管和旧 Worker 写入隔离。

`yolo_bm1688_export`和`yolo_edge_validation_job`各需要上述5列，共10列；
`yolo_bm1688_export`另外需要可空的`worker_action`（`export`/`recompile`）和单列索引，
用于把手工导出、手工重新编译统一交给同一个独立 Worker。历史空值按`export`兼容处理。
所有 owner 和 expires 字段允许为空，历史行不回填；fencing token 为非空整数，
历史行默认0，首次领取递增为1。

## 2. 第 08 段必须包含的索引

每张表保留以下5个索引，共10个：

- `queue_lease_owner`单列索引，与ORM结构一致并用于所有者诊断。
- `queue_lease_expires_at`单列索引，用于到期租约排查和恢复扫描。
- `execution_lease_owner`单列索引，用于执行所有者诊断。
- `execution_lease_expires_at`单列索引，用于失联执行定位。
- BM1688导出表使用`(status, worker_action, queue_lease_expires_at, id)`复合索引，
  覆盖自动和手工导出/重编译的统一Worker候选扫描；列序仍需在生产规模快照上以
  `EXPLAIN`按真实数据分布确认。
- 边端验证表使用`(status, queued_at, id)`复合索引，匹配当前真实调度器按待处理
  状态和入队时间扫描的路径；当前没有独立Edge Worker，不预设不存在的扫描逻辑。

fencing续租、条件写入和释放都先按主键`id`定位，再校验owner、token和到期时间，
因此不增加低选择性的token单列索引。若真实MySQL执行计划证明复合索引未被使用，
应先根据生产快照分布调整复合列顺序，不能盲目增加更多重复索引。

## 3. 当前候选DDL差异

隔离候选`gai_p1_08_add_dispatch_leases.py`现已覆盖三表15个租约字段、1个Worker动作列、
12个租约单列索引、1个动作单列索引和3个Worker扫描复合索引，并验证重复升级幂等和降级仅移除租约对象。但它仍不是
完整第08段，缺少Task、附件、数据源、第三方回流审计和模型版本关联等兼容字段。

当前空库ORM通过字段上的`index=True`只能生成8个单列索引，不会自动生成2个
Worker扫描复合索引。正式迁移落地时必须同步ORM的`__table_args__`，否则空库建表
和从生产基线升级后的结构不一致。

## 4. MySQL隔离验收

只允许本机、库名以`labelu_migration_sandbox`开头的实例执行。禁止把生产连接串
传给演练脚本。

### 4.1 结构与默认值

1. 在生产同版本MySQL建立空沙箱和脱敏生产快照副本。
2. 升级前保存`SHOW CREATE TABLE`、`SHOW INDEX`和表行数。
3. 执行第08段两次，第二次不得重复建列或索引。
4. 核对两表各5列、5索引；四个租约字段可空，token非空且默认0。
5. 抽查升级前历史行：租约字段为NULL、token为0，业务状态和制品路径不变。
6. 核对字符集、排序规则、外键、AUTO_INCREMENT和行数没有非预期变化。

### 4.2 执行计划

使用与Worker一致的查询在生产快照副本执行`EXPLAIN FORMAT=JSON`：

```sql
SELECT id
FROM yolo_bm1688_export
WHERE status = 'PENDING'
  AND worker_action IN ('export', 'recompile')
  AND (queue_lease_expires_at IS NULL OR queue_lease_expires_at <= NOW())
ORDER BY id
LIMIT 20;
```

边端调度另用`status = 'PENDING' AND queued_at IS NOT NULL ORDER BY queued_at, id`
执行计划验收。BM1688候选key应包含`worker_scan`，边端候选key应包含
`dispatch_scan`；在生产规模快照上不能退化为全表扫描，扫描行数、排序临时表和
单次耗时必须记录。边端领取动作还会按主键执行带queue owner/expiry条件的
UPDATE，`dispatch_scan`只服务候选扫描，不代表UPDATE已完成并发验收。由于`OR`
条件可能受数据分布影响，不能只看“索引存在”就宣称性能通过。

### 4.3 锁等待和在线DDL

1. 执行前检查长事务和`performance_schema.metadata_locks`，存在阻塞者立即停止。
2. 演练Session设置短`lock_wait_timeout`；超时必须失败退出，禁止自动无限重试。
3. 在与生产同版本MySQL验证实际DDL支持的`ALGORITHM`和`LOCK`级别，记录每条
   ALTER/CREATE INDEX耗时；不能未经验证直接承诺`INSTANT`或`LOCK=NONE`。
4. DDL期间持续记录接口错误率、数据库连接池等待、P95/P99延迟和慢查询。
5. 生产窗口只允许加列/加索引；不在同一窗口执行历史全表回填或删除旧对象。

### 4.4 并发与fencing

1. 两个进程同时领取同一编号，条件UPDATE只能一个返回1。
2. 活跃执行租约未过期时，第二个owner领取失败。
3. 租约过期后接管成功且token严格加1；旧token续租、状态写入和释放均返回0。
4. Worker执行期间续租不持有长业务事务；每次副作用和终态写入前校验token。
5. 模拟Worker断电、数据库短断和锁等待超时，确认任务可接管且制品不会重复发布。

## 5. 当前证据边界

本地仅完成SQLite候选DDL、索引列顺序和安全闸门测试。尚未提供本机MySQL沙箱、
生产快照副本或真实并发压测，因此MySQL执行计划、元数据锁、在线DDL能力和生产
耗时均为待验收项，不能标记为通过。
