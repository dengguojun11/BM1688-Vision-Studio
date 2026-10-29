# RUN-0098 远程训练制品 HMAC 签名校验

- 日期和时区：2026-09-18，Asia/Shanghai。
- 方案章节和执行契约：主方案第 8、11、15.7 节；`REMOTE-ARTIFACT-HMAC-01`。
- 工作包和角色：WP2 训练与执行器；R03 架构、R04 后端主责，R06 模型算法、R09 测试协作。
- 本轮范围：不新增用户角色、不新增项目/业务空间、不改菜单；只保护远程训练传输和回传制品。

## 为什么需要这一轮

RUN-0097 的 SHA256 只能说明文件内容与平台预期摘要一致。若远程节点被误配置或被替换，攻击者可以重新计算 SHA256 并伪造阶段文件、训练结果或 `best.pt`。本轮加入平台与受管 Agent 共享的 HMAC-SHA256 密钥，只有掌握密钥的一方才能生成平台接受的制品。

## 具体协议

1. 平台生成 `job_spec.json` 后，使用 `YOLO_REMOTE_TRAIN_HMAC_SECRET` 签名，写入 `hmac_key_id` 和 `hmac_sha256`。签名字段本身不参与签名，JSON 使用排序键和紧凑分隔符规范化。
2. 平台通过 SSH/SFTP 将密钥写入远程作业目录的 `.hmac-key`，权限设为 `600`。密钥不进入 payload、页面、数据库作业清单、启动命令或日志；Agent 结束后删除该文件。
3. Agent 启动时先验签 `job_spec.json`。阶段文件 `phase.json`、`agent_result.json` 和失败回传都带 HMAC；训练完成后对 `train/weights/best.pt` 生成 `best.pt.hmac.json` 旁车签名。
4. 平台轮询阶段、导入结果和接收 `best.pt` 前验签，同时继续校验操作世代、`spec_sha256`、类别契约和执行租约。任意一项失败都忽略本次回传，不覆盖当前作业。

## 环境策略

- 开发/测试环境未配置密钥时，保留历史 Agent 的无签名兼容，便于本地单机训练和旧节点迁移。
- 配置了密钥时，即使不是生产环境也要求收到的 JSON 和 `best.pt` 带正确签名，防止测试误把旧制品当新制品。
- `APP_ENV=prod` 或 `APP_ENV=production` 时无论 `YOLO_REMOTE_TRAIN_HMAC_REQUIRED` 取值如何，平台都要求 `YOLO_REMOTE_TRAIN_HMAC_SECRET`，否则在领取执行租约前拒绝派发；Agent 也拒绝无密钥启动。
- 密钥版本由 `YOLO_REMOTE_TRAIN_HMAC_KEY_ID` 表示。当前实现一次只接受活动版本，轮换应先在平台和受管节点切换到同一新版本，再重新派发作业；旧作业需完成或人工清理后再撤销旧密钥。

## 并发与失败行为

- 签名校验发生在数据库条件更新之前，迟到的旧 Agent 不能通过 HMAC 绕过 owner/token 和操作世代隔离。
- 上传/下载中断、Agent 重启、阶段文件缺失、JSON 被修改、`best.pt` 被替换或旁车签名缺失时，平台保持当前作业状态并等待当前世代的合法回传，不会把错误制品导入模型中心。
- 密钥文件只在远程作业目录短暂存在。Agent 正常完成或失败时删除；节点异常掉电后需由运维按作业目录清理残留密钥文件。

## 本地证据

```text
python -m pytest labelu/tests/internal/common/test_remote_train_hmac.py -q --disable-warnings --tb=short
6 passed

python -m pytest labelu/tests/scripts/test_remote_train_agent.py labelu/tests/internal/application/service/test_remote_yolo_train_runtime.py labelu/tests/internal/application/service/test_remote_payload_download.py -q --disable-warnings --tb=short
27 passed

python -m py_compile labelu/internal/common/remote_train_hmac.py labelu/internal/application/service/remote_yolo_train.py labelu/scripts/remote_train/agent_worker.py
通过
```

## 尚未完成的生产验证

- 尚未在真实生产 MySQL、远程 GPU 节点、systemd 重启、断点续传和节点异常掉电场景演练密钥文件清理。
- 尚未接入生产密钥管理系统；当前密钥来源是部署环境变量，生产上线前应由 Secret 管理器注入并限制读取权限。
- 当前未做多版本并行验签和自动密钥轮换；轮换由运维按 `YOLO_REMOTE_TRAIN_HMAC_KEY_ID` 变更窗口执行。

本轮结论：远程训练已具备本地 L1 的 HMAC 签名与生产缺钥 fail-closed 代码路径；真实节点、Secret 管理器和轮换演练仍是生产放行前置条件。本轮未提交、未推送、未部署。
