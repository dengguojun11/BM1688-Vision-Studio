# RUN-0292 Board Agent 固定评估集与质量门禁

## 目标

收口 Board Agent 与 SSH 真机验证共用的固定评估集契约。模型包必须携带同一批评估图片、完整标签、文件摘要、固定集签名和质量规则快照；板端执行器在推理前失败关闭，禁止使用缺标签或被篡改的评估包。

## 本轮产出

| 角色 | 工作包 | 产出 |
|---|---|---|
| R06 模型算法架构 | WP3 | Board Agent `board_eval/` 生成 `fixed_eval.v1` 清单，包含图片、YOLO TXT/XML 标签、文件大小和 SHA256 |
| R07 BM1688 边端 | WP3 | `run_config.json` 写入 `fixed_eval_signature`、`eval_image_count`、`labeled_image_count`、`require_fixed_eval_binding`、`quality_rule`；包根同步 `manifest.json` |
| R04 后端 | WP2/WP3 | SSH 固定评估清单补齐同一标签数量字段，避免两条真机验证链使用不同门禁契约 |
| R09 测试 | WP5 | Board Agent 打包复用、清单完整性、篡改和缺标签门禁测试 |

## 关键规则

1. `board_eval/manifest.json` 必须为 `fixed_eval.v1`，包含图片摘要、标签摘要、`image_count`、`labeled_image_count`、`label_mappings` 和 `manifest_sha256`。
2. 图片和标签任一摘要、大小或路径不一致，固定评估包视为不可复用，重新生成或阻断。
3. `run_config.json.fixed_eval_signature` 必须等于固定集清单摘要，`require_fixed_eval_binding=true`。
4. `eval_image_count` 与固定集图片数绑定，`labeled_image_count` 必须大于 0，并与清单一致。
5. `quality_rule` 必须来自导出制品的版本化 BM1688 质量规则快照；缺失时由板端门禁失败关闭，不使用运行时默认值。
6. SSH 与 Board Agent 均使用上述字段契约，不能因为验证通道不同而放宽固定评估集要求。

## 修改范围

- `labelu/internal/application/service/board_eval_pack.py`
- `labelu/internal/application/service/board_agent.py`
- `labelu/internal/application/service/edge_ssh_validation.py`
- `labelu/scripts/edge_board_validate_runner.py`
- `labelu/tests/internal/application/service/test_board_agent_pack.py`
- `labelu/tests/scripts/test_edge_board_validate_runner_script.py`

## 本地验证

```text
python -m pytest labelu/tests/internal/application/service/test_board_agent_pack.py labelu/tests/scripts/test_edge_board_validate_runner_script.py -q --disable-warnings
18 passed

python -m pytest labelu/tests/internal/application/service/test_yolo_edge_validation_pack.py -q --disable-warnings
13 passed

python -m compileall -q labelu/internal/application/service/board_eval_pack.py labelu/internal/application/service/board_agent.py labelu/scripts/edge_board_validate_runner.py
通过
```

## 环境边界与验收

- 本轮仅完成本地代码和测试，未连接生产 MySQL、AICM、S3、真实 TPU-MLIR 或 BM1688。
- 未执行真实板端 HIL，不能宣称真实设备验证通过。
- 未提交、未推送、未部署。
- 本地验收等级：L1（代码契约和本地模拟门禁通过）；尚未具备同版本 MySQL/预生产并发证据，因此不能标记 L2。生产放行仍需真实 BM1688 包下载、标签完整性、三种 BModel 推理和回传证据。
