# 项目名称

本项目的工程目录和长期维护名称为 **7G_AI_labelU**。

- 本地源码目录：`D:\PycharmProjects\7G_AI_labelU`
- 生产部署根目录：`/data/7G_AI`
- 生产访问地址：`http://113.141.72.175:18001/`

Python 包目录仍保留为 `labelu/`，CLI 命令仍保留为 `labelu`，这是为了兼容现有导入路径、数据库迁移和生产 Worker。产品界面品牌继续使用“7GAI 进化平台”。

后续本地代码修改、测试和提交均以本目录为唯一源码目录；生产发布只更新 `/data/7G_AI/app`，不修改旧项目 `/data/labeIU`。
