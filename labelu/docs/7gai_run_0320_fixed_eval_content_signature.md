# RUN-0320 固定评估集内容签名与 Board Agent/SSH 一致性回归（2026-09-21）

## 1. 本轮目标

修复 RUN-0319 暴露的评估集口径风险：Board Agent 使用 `board_eval/` 目录，SSH 兼容通道使用 `eval_data/images/test/` 目录，二者不能直接比较传输清单摘要。若两条通道各自重新抽样，即使图片数量相同，也可能对不同图片给出质量结论。

本轮将固定评估集身份定义为“图片和标签内容身份”，而不是传输目录身份。仅在本地代码和测试环境执行，不连接生产，不修改生产数据，不提交、不推送、不部署。

## 2. 代码交付

| 角色 | 工作包 | 本轮交付 | 验收边界 |
|---|---|---|---|
| R03 架构 | WP3 | 新增固定评估集内容签名模块，签名只依赖文件类别、文件名、大小、SHA256、图片/标签数量和类别顺序 | 目录层级变化不改变签名；内容变化必然改变签名 |
| R04 后端 | WP3 | Board Agent 打包清单写入 `fixed_eval_signature`；SSH 构包优先复用已生成的 Board Agent 固定包，并对签名不一致直接失败 | 不允许 SSH 在已有固定包时重新抽取另一批图片 |
| R06 模型算法 | WP3 | 统一图片与 YOLO TXT/XML 标签的固定集身份；标签缺失、清单篡改仍由原有门禁阻断 | 不改变 YOLO 矩形框和三精度质量规则 |
| R07 BM1688 边端 | WP3 | `run_config` 和板端脚本优先使用内容签名，兼容历史仅有 `manifest_sha256` 的旧包 | 旧包可继续验证，新包必须绑定内容签名 |
| R09 测试 | WP5 | 增加目录布局无关签名、内容变化检测、SSH 复用固定包和旧格式拒绝测试 | 专项回归必须全通过 |
| R10 运维 | OPS | 保持生产 Worker、Board Agent 主切换和自动发布开关关闭 | 本轮不宣称生产 HIL 或生产部署完成 |

## 3. 实现规则

### 3.1 固定集签名

`fixed_eval_content_signature()` 对清单中的每个文件提取：

- 文件类别：图片、YOLO 标签或 XML 标签；
- 文件名、文件大小和 SHA256；
- 图片数量、有标签图片数量；
- 类别名称及其顺序。

文件路径只用于识别类别，不参与最终摘要。因此 Board Agent 的 `images/0000.jpg` 和 SSH 的 `images/test/0000.jpg` 在内容相同的情况下得到同一签名；替换图片或标签内容会改变签名。

### 3.2 两条真机通道

- Board Agent 是目标主路径，负责领取任务、下载模型包、在 BM1688 上执行和回传报告。
- SSH 是现有兼容路径，不是另一套评估规则。发现 `board_eval` 固定包时，SSH 只转换目录布局并复用同一批图片、标签和 `fixed_eval_signature`。
- 既有固定包缺失、格式错误或签名不一致时，SSH 构包失败关闭，不静默改用新的训练目录抽样。

### 3.3 历史兼容

旧包没有 `fixed_eval_signature` 时继续使用 `manifest_sha256`，保证历史作业可读取；新包一律生成内容签名。发布门禁仍要求新作业具备固定集绑定和标签完整性证据。

## 4. 本地验证

```text
python -m pytest \
  labelu/tests/internal/application/service/test_board_eval_pack.py \
  labelu/tests/internal/application/service/test_board_agent_pack.py \
  labelu/tests/internal/application/service/test_edge_ssh_fixed_eval.py \
  labelu/tests/scripts/test_edge_board_validate_runner_script.py \
  -q --disable-warnings --tb=short
23 passed, 114 warnings

python -m pytest \
  labelu/tests/internal/application/service/test_yolo_edge_validation_pack.py \
  labelu/tests/internal/application/service/test_board_agent_unified.py \
  labelu/tests/internal/application/service/test_board_agent_device_channel.py \
  labelu/tests/internal/common/test_edge_validation_evidence.py \
  labelu/tests/internal/common/test_release_gate.py \
  -q --disable-warnings --tb=short
30 passed, 116 warnings

python -m compileall -q \
  labelu/internal/common/fixed_eval_signature.py \
  labelu/internal/application/service/board_eval_pack.py \
  labelu/internal/application/service/board_agent.py \
  labelu/internal/application/service/edge_ssh_validation.py \
  labelu/scripts/edge_board_validate_runner.py
通过
```

全量后端回归：

```text
python -m pytest labelu/tests -q --disable-warnings --tb=short
2710 passed, 56 skipped, 420 warnings in 451.07s
```

退出码为 `0`。跳过项是既有环境条件跳过，不是本轮固定评估集改造失败。

## 5. 仍未完成的生产项

- 生产 `export_id=226` 的历史证据仍存在 32/50 张评估集口径差异；本轮代码修复不追溯改写历史生产结果。
- 需要在预生产使用同一个真实 `DatasetVersion` 重新产生 PT、ONNX、FP32、FP16、INT8 和 BM1688 报告，确认五段链路都记录同一个 `fixed_eval_signature`。
- 仍缺真实 MySQL 多进程租约、Board Agent 断网重领、设备重启接管、P95/短稳、AICM/本平台下载目录/S3 三方对账证据。
- 因生产版本尚未对齐，所有相关 Worker 和自动发布开关继续失败关闭。

## 6. 交付状态

本轮达到本地 L1 代码和专项测试验收。未连接生产、未执行生产写入、未提交、未推送、未部署；等待用户明确“提交代码”后再按既定流程进入提交、部署和在线验证。
