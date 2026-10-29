# RUN-0333 新建标签英文系统值与自动颜色

- 日期：2026-09-21
- 责任角色：R01 产品、R04 后端、R08 前端/UI、R09 测试
- 工作包：WP2、WP4、WP5
- 需求：新建标签中的系统值明确标注为英文；颜色自动分配且不与已有标签重复；保持居中弹窗和中文页面。

## 代码与业务路径

- 页面：`frontend/labelU-Kit/apps/frontend/src/pages/platform/pages/admin.tsx`
- 测试：`frontend/labelU-Kit/apps/frontend/src/pages/platform/pages/admin.test.tsx`
- 创建接口：`POST /v1/annotation-taxonomy/labels`
- 后端唯一性来源：`labelu/internal/application/service/system_labels.py` 的 `_next_available_label_color` 和 `_assert_unique_color`，以及 `annotation_label.color` 唯一约束。

## 本轮改动

1. 标签表和新建标签弹窗的字段名称改为“系统值（英文）”，并补充只能以英文字母开头、使用字母/数字/下划线的中文说明。原“创建后不能修改”规则已由 RUN-0340 修正为管理人员可修改、历史快照不变。
2. 新建标签时根据当前已加载标签计算一个未占用的色号作为预览；颜色框只读展示，避免用户误以为需要手工维护。
3. 新建请求不再发送固定的 `#1677ff` 或前端预览色号，交由后端在事务内自动分配并再次校验唯一性，降低并发创建时前端缓存过期造成重复的风险。
4. 编辑已有标签仍允许调整颜色，后端继续校验全局唯一并返回冲突提示。

## 验收证据

- 前端回归：`pnpm exec vitest run src/pages/platform/pages/admin.test.tsx --reporter=dot --silent`，`5 passed`。
- 类型检查：`pnpm run type-check`，退出码 `0`。
- 浏览器：`http://127.0.0.1:8002/annotation-settings` 重定向到系统管理标注治理页；新建弹窗显示“系统值（英文）”、自动颜色预览和“保存时再次校验”说明。
- 浏览器验收未点击创建，未写入本地业务数据；本地服务仍为 `labelu --host 127.0.0.1 --port 8002`。

## 状态与遗留

- 状态：本地完成，等待用户验收。
- 未连接生产、未提交、未推送、未部署、未重启生产。
- 真实生产并发仍需在 MySQL 环境验证；真实 BM1688、AICM、S3 等外部证据不受本轮 UI 改动影响。
