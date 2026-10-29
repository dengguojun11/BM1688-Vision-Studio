# SOP 3.6.2 服务端评估脚本

当前生效脚本以本目录为准，供 LabelU 在 BM1688 导出完成后做服务端补充评估或组装边端验证执行包。

| 脚本 | 运行位置 | 说明 |
|------|----------|------|
| `cloud_based_evaluation.py` | LabelU 服务端 | 评估 `best.pt` |
| `onnx_based_evaluation.py` | LabelU 服务端 | 评估导出后的 ONNX |
| `pythonTest_evaluation.py` | 算能边端或边端验证包内 | 产出 `server_eval/results_bmodel/evaluation_report.md` 等结果 |

结果目录通常位于：

`MEDIA_ROOT/yolo_bm1688_exports/<export_id>/server_eval/`

相关开关：

- `YOLO_SOP_SERVER_EVAL_ENABLED`
