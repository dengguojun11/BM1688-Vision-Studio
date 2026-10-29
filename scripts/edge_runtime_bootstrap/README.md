# BM1688 边端离线环境包

该包只负责环境检测和搭建，不包含业务模型或待验证图片。目标设备为与生产参考设备一致的 BM1688 ARM64 官方系统。

## 系统边界

设备必须已经刷入匹配的 BM1688 官方系统，并具备：

- `aarch64` 架构；
- `/dev/bmdev-ctl`、`/dev/bm-tpu0` 或 `/dev/bm-sophon0`；
- `/opt/sophon/libsophon-current`；
- Docker 服务。

这些驱动、固件和宿主机库必须由设备官方镜像提供。环境包离线补齐 `7g` 推理容器、Sophon Demo 和 LabelU 通用模型验证程序，可运行普通 YOLO 检测、车牌检测单模型和完整车牌识别模型。

## 首次安装

```bash
tar -xf bm1688-edge-runtime-offline.tar
cd bm1688_edge_runtime
sudo bash setup.sh
```

安装完成后 `environment_report.json` 中必须为：

```json
{"environment_ready": true}
```

也可以随时重新检查：

```bash
sudo bash check_env.sh
```

## 执行模型验证包

把模型验证包上传到设备 `/data` 下，然后执行：

```bash
sudo bmodel-validate /data/bmodel-validation-job.tar.gz
```

结果保存在 `/data/labelu_edge/runtime/validator/results/`。
