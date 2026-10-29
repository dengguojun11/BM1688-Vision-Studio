# RUN-0178 模型目录角色与下载禁用门禁

- 日期：2026-09-20，Asia/Shanghai。
- 责任角色/工作包：R01 产品、R04 后端、R08 前端、R09 测试；WP3/WP5。
- 目标：落实模型中心的真实业务边界：发布成功后模型必须同时保留在 AICM 和 7GAI 模型目录；目录使用 AICM `model_set_id` 作为稳定模型 ID；标注人员可以发起模型发布；已发布模型出现问题时只关闭后续下载，不删除目录、不回滚已经发出的外部模型。

## 现状核对

1. `POST /api/v1/yolo-aicm-publish/publish` 使用登录用户依赖，不要求系统管理员，因此标注人员可以发起发布。AICM 发布成功后由 `register_published_model` 写入/更新 `published_model_catalog`，并将训练作业对应的 `ModelVersion` 关联到目录。
2. 目录的 `public_model_id` 与 `aicm_model_set_id` 均使用 AICM `model_set_id`；AICM 别名由系统维护，第三方回流可以按该 ID 对齐模型。
3. 模型包发布成功后复制到 `published_model_packages/{model_set_id}/model.zip` 长期保留。已有包不会被不同内容自动覆盖；本地包存在时下载不依赖 AICM 在线可用。
4. 管理人员通过目录的 `download_enabled` 开关停止后续下载。开关使用“期望旧值 + 条件更新”，并发修改冲突返回 409；关闭下载只保留目录记录和血缘，不做模型回滚或删除。
5. 目录接口后端已经限制同步、目录编辑、别名维护和下载开关为系统管理员；发布接口仍对已登录的标注人员开放。

## 本轮修改

- 模型列表页面调用 `/api/v1/users/me` 读取 `is_task_superuser`。
- 管理人员看到并可使用：同步、目录编辑、对外开放、允许下载、第三方别名维护。
- 标注人员看到：模型列表、详情、当前可下载的模型包；管理操作区隐藏，并显示“下载由管理人员控制”。后端权限仍是最终防线，不能依赖前端隐藏保证安全。
- 下载按钮在 `download_enabled=false` 时禁用；服务端下载接口再次校验该开关，禁止通过直接 URL 绕过页面。
- 兼容旧版 `/users/me` 未返回 `is_task_superuser` 的环境：页面暂不误隐藏管理区，但所有写接口仍由后端 `require_system_admin` 拒绝越权；正式部署后应以带该字段的版本为准。

## 验证

- `python -m pytest labelu/tests/internal/adapter/routers/test_published_model_catalog.py labelu/tests/internal/application/service/test_published_model_catalog.py -q`：22 passed。
- `python -m pytest labelu/tests/e2e/test_yolo_model_catalog_static.py -q`：`2 passed`。
- 未执行生产写入、AICM 发布、模型下载、数据库迁移或部署。

## 后续真实环境放行条件

1. 用标注人员账号验证发布接口可创建发布任务，且发布成功后 AICM `model_set_id`、7GAI 目录和回流关联 ID 一致。
2. 用管理人员账号验证关闭下载后，公开目录不再返回该模型，已登录下载接口和公开下载接口均拒绝；目录详情仍可查询。
3. 用标注人员账号验证只能查看/下载，不能同步目录、改展示状态、改下载开关或维护第三方别名。
4. 在同版本 MySQL 与多进程服务下验证目录唯一约束、下载开关条件更新和缓存下载的锁等待；在证据完成前不宣称生产通过。

## 交付状态

- 本地代码与专项测试：本轮完成。
- 生产：未提交、未推送、未部署、未写入，等待用户明确“提交代码”。
