# RUN-0192 任务列表摘要并发加载与前端类型收口

**日期**：2026-09-20（Asia/Shanghai）  
**环境级别**：本地 L1；未提交、未推送、未部署  
**责任角色**：R03 架构、R08 前端/UI、R09 测试  
**关联工作包**：WP0 菜单主链、WP5 测试验收

## 1. 本轮目标

减少任务列表页重复请求和摘要等待，统一矩形框预置标签摘要缓存时间，并消除完成页
国际化类型深层推导导致的前端类型检查失败。

## 2. 修改内容

- `frontend/labelU-Kit/apps/frontend/src/loaders/task.loader.ts`
  - 增加 `RECT_PRESET_SUMMARY_STALE_TIME_MS = 60 * 1000`。
  - 任务列表、标签摘要、数据集分类摘要通过 `Promise.all` 并发读取。
  - 标签摘要查询键包含分类/未分类范围，避免跨筛选条件复用错误缓存。
  - 返回摘要数据供任务页复用，避免首屏再次发起同一请求。
- `frontend/labelU-Kit/apps/frontend/src/pages/tasks.[id].samples.finished/index.tsx`
  - 显式指定 `translation` 命名空间，避免 `react-i18next` 默认命名空间递归推导。

## 3. 并发与一致性

- 三个只读请求并行执行，不新增后端锁和写操作。
- 摘要缓存 60 秒，任务列表缓存 30 秒；筛选范围进入查询键，避免脏读。
- 任务创建、标注保存、训练提交和 Worker 租约逻辑未改变。

## 4. 本地证据

- `node --test frontend/extensions/tests/*.test.mjs`：`22 passed`。
- `pnpm --filter @labelu/frontend type-check`：通过。
- `pnpm --filter @labelu/frontend build`：通过；保留既有 Ant Design `use client`、代码分包和 `eval` 警告。
- 任务 Loader 缓存契约由 `task_loader_cache.test.mjs` 覆盖。

## 5. 生产边界

未连接生产、未修改生产前端、未提交、未推送、未部署。生产部署后仍需用两类账号验证任务
列表筛选、标签摘要和新建任务页的真实 API 复用情况。
