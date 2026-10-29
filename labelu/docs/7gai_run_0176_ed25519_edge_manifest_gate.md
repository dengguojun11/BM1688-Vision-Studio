# RUN-0176 服务器 Ed25519 签名与 BM1688 边端清单验签门禁

## 目标

让服务器生成的 BM1688 验证包在 SSH 和 Board Agent 两条执行路径上都先验签、再加载 BModel。BM1688 只持有公钥，不持有服务器 HMAC 或 Ed25519 私钥；验签失败、文件被替换、模型身份不一致或缺少三种精度时，边端不得开始推理。

## 本轮实现

- 新增 `sign_model_manifest_ed25519` / `verify_model_manifest_ed25519`，支持服务器 PEM、base64、hex 和 32 字节密钥；签名字段不参与自身摘要，AICM 回填字段不破坏签名。
- 新增 `EDGE_MANIFEST_ED25519_PRIVATE_KEY`、`EDGE_MANIFEST_ED25519_KEY_ID`、`EDGE_MANIFEST_ED25519_REQUIRE` 配置。私钥只在服务器配置，不能写入 `run_config.json` 或下发到板端。
- Board Agent 打包的 `run_config.json` 携带制品清单、`profile_digest`、导出编号、执行世代和三精度要求；配置私钥时由服务器签名，生产环境缺私钥时明确标记 `MISSING_KEY` 并在边端失败关闭。
- `scripts/ops/labelu_board_agent.py` 在解压后校验 Ed25519 公钥、key_id、导出编号、执行世代、Profile 摘要、制品路径和 SHA256，以及 FP32/FP16/INT8 完整性。
- SSH 使用的 `edge_board_validate_runner.py` 复用同一 Board Agent 验签函数，避免 SSH 和主动拉取路径出现两套规则。
- 未标记强制验签的历史开发包仍保持兼容读取；生产生成包通过 `edge_signature_required` 强制缺签名即失败。

## 验证证据

- `python -m pytest labelu/tests/internal/common/test_model_manifest_signature.py labelu/tests/internal/application/service/test_board_agent_pack.py labelu/tests/internal/application/service/test_edge_board_validate_runner.py -q`
- 结果：`15 passed`。
- 未连接生产、AICM、S3、TPU-MLIR 或真实 BM1688，未进行生产写入、提交、推送和部署。

## 未完成放行项

真实部署时仍需为 Board Agent/SSH runner 预置公钥，执行真机三精度加载、篡改包、错误 key_id、旧执行世代重放和重启接管测试；当前本地验签通过不等于真实 BM1688 已验收。
