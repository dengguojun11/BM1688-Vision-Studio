# RUN-0266 BM1688 制品下载路径与清单校验修复（2026-09-20）

## 发现

R06/R10 对模型中心和边端下载链做只读审查时发现，`GET /api/v1/yolo-bm1688-exports/{id}/bmodel` 在指定 `filename` 时曾直接把用户输入拼接到导出目录，缺少解析后的目录归属校验，也没有强制要求文件出现在该导出的 BModel 清单中。仅凭任务权限和导出状态不能证明下载文件是登记过的模型制品。

## 修改

- 规范化文件名为 POSIX 相对路径，绝对路径和包含 `..` 的路径直接返回 `403`。
- 对解析后的路径执行 `relative_to(models_root)`，确保文件不能跳出当前导出的 `models` 目录。
- 只允许 `_list_bmodel_names(export_id)` 返回的清单项，未登记或不存在的文件返回 `404`。
- 数据库中的默认 `bmodel_rel_path` 同样经过当前导出目录和清单校验，不再因为历史字段可写就放宽下载范围。
- 没有改变本方案的硬约束：FP32、FP16、INT8 三种 BModel 都必须生成并进入同一模型版本的制品清单；下载时只允许清单中的文件。发布门禁仍需同时核对三种制品、摘要、配置档案和真机证据。

## 验证

```text
python -m pytest labelu/tests/internal/application/service/test_yolo_bm1688_export_auto_resume.py -q --disable-warnings --tb=short
43 passed

python -m pytest labelu/tests/internal/application/service/test_bm1688_compile_flow.py labelu/tests/internal/application/service/test_yolo_bm1688_edge_business_flow.py labelu/tests/internal/adapter/routers/test_yolo_bm1688_exports.py -q --disable-warnings --tb=short
通过（含 1 个既有跳过项）
```

## 生产边界

- 未连接生产、未下载生产文件、未修改数据库、未提交、未推送、未部署。
- 真实 BM1688 真机、Board Agent/systemd、签名公钥、固件和设备序列号对账仍未完成；本地测试不能替代硬件放行证据。
- 兼容 Board Agent 旧下载链仍需按 RUN-0264 的设备身份、租约和制品签名清单进一步收口。

## 状态

- 本地代码：L1，路径安全修复完成。
- 生产放行：未完成，等待真实设备和发布门禁证据。
