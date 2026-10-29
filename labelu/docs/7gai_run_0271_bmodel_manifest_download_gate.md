# RUN-0271 BModel 下载制品清单与摘要校验（2026-09-20）

## 发现

BM1688 下载接口已经限制导出目录和文件名，但当导出记录存在 `artifact_manifest_json` 时，仍未用清单中的文件摘要校验磁盘文件。编译产物被替换后，文件名和目录都可能合法，仍有被下载的风险。

## 修改

- `resolve_bmodel_download_path()` 在清单存在时强制查找 `role=model_bmodel` 的对应相对路径。
- 校验清单记录的 `bytes` 和 64 位 SHA256。
- 清单缺失、格式异常、路径未登记、大小不一致或摘要不一致均拒绝下载。
- 历史没有 `artifact_manifest_json` 的记录保留目录/清单文件名兼容逻辑，不伪造新证据。
- 普通下载、边端验证包和 AICM 发布仍继续使用同一导出制品目录，避免多套文件真源。

## 验证

```text
python -m pytest labelu/tests/internal/application/service/test_yolo_bm1688_export_auto_resume.py labelu/tests/internal/application/service/test_bm1688_compile_flow.py labelu/tests/internal/application/service/test_yolo_bm1688_edge_business_flow.py -q --disable-warnings --tb=short
52 passed, 114 warnings
```

新增回归覆盖：清单正确时下载通过；磁盘文件被替换后摘要校验失败；路径穿越和未登记文件继续失败。

## 生产边界

- 未连接生产、未读取生产模型文件、未修改数据库、未提交、未推送、未部署。
- 真实 BM1688 三精度制品、Manifest 签名、公钥消费和 AICM/平台目录对账仍未完成。

