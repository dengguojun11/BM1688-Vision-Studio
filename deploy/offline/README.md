# BM1688 离线资源包

服务器访问 GitHub 不稳定时，在**本机或任意网络好的机器**打包，再传到服务器由 `deploy.sh` 自动解压（**优先于 git clone**）。

## 1. 打包（推荐 Gitee 源，国内可访问）

```bash
# Linux / 服务器
cd /path/to/labelU
export TPU_MLIR_GIT_URL=https://gitee.com/mirrors/TPU-MLIR.git
bash scripts/ops/pack_offline_assets.sh all
# 生成 deploy/offline/tpu-mlir.tar.gz、sophon-demo.tar.gz
# BM1688 Docker 量化镜像（可选，数 GB）：
#   wget -O deploy/offline/sophgo-tpuc_dev-v3.2_191a433358ad.tar.gz \
#     https://sophon-file.sophon.cn/sophon-prod-s3/drive/24/06/14/12/sophgo-tpuc_dev-v3.2_191a433358ad.tar.gz
```

已在服务器生成时可直接：

```bash
# 已有 /data/labeIU/sophon-demo 与 gitee 克隆的 tpu-mlir 时
tar -czf /data/labeIU/offline/tpu-mlir.tar.gz -C /path/to/parent tpu-mlir
tar -czf /data/labeIU/offline/sophon-demo.tar.gz -C /data/labeIU sophon-demo
sudo bash scripts/ops/publish_offline_bundles.sh   # Git LFS 推送到 labelU 仓库
```

Windows PowerShell 可先 WSL 执行上述命令，或自行：

```powershell
git clone --depth 1 https://github.com/sophgo/tpu-mlir.git $env:TEMP\tpu-mlir
tar -czf deploy/offline/tpu-mlir.tar.gz -C $env:TEMP tpu-mlir
```

## 2. 传到服务器（推荐，不增大 git 仓库）

```bash
ssh root@113.141.72.175 -p 12043 "mkdir -p /data/labeIU/offline"
scp -P 12043 deploy/offline/tpu-mlir.tar.gz deploy/offline/sophon-demo.tar.gz \
  root@113.141.72.175:/data/labeIU/offline/
```

## 3. 或 push 进私有 Git（可选）

```bash
git add -f deploy/offline/tpu-mlir.tar.gz deploy/offline/sophon-demo.tar.gz
git commit -m "chore: add offline tpu-mlir and sophon-demo bundles"
git push origin main
```

默认 `.gitignore` 忽略 `*.tar.gz`，需 `-f` 强制添加。单包可能数百 MB，更推荐 scp 到 `/data/labeIU/offline/`。

## 4. 服务器部署

```bash
export LABELU_DEPLOY_OFFLINE_DIR=/data/labeIU/offline
# 或把包放在仓库 deploy/offline/ 下

cd /data/labeIU/labeIuproject/labeIU
git pull origin main
sudo env LABELU_DEPLOY_OFFLINE_DIR=/data/labeIU/offline \
  LABELU_DEPLOY_OVERWRITE_ENV=0 INSTALL_EXTRAS=yolo,image-pipeline \
  bash deploy.sh restart
```

## 查找顺序

| 资源 | 环境变量 | 默认文件名 |
|------|----------|------------|
| TPU-MLIR | `TPU_MLIR_OFFLINE_ARCHIVE` | `tpu-mlir.tar.gz` |
| sophon-demo | `SOPHON_DEMO_OFFLINE_ARCHIVE` | `sophon-demo.tar.gz` |
| BM1688 Docker | `LABELU_BM1688_DOCKER_TAR` | `sophgo-tpuc_dev-v3.2_191a433358ad.tar.gz` |

搜索目录（按序）：

1. 上述环境变量指向的文件  
2. `$LABELU_DEPLOY_OFFLINE_DIR`（如 `/data/labeIU/offline`）  
3. `$LABELU_DEPLOY_BASE/offline`  
4. 仓库 `deploy/offline/`

解压后须包含 `envsetup.sh`（TPU-MLIR）或 `sample/YOLOv8_plus_det/scripts`（sophon-demo）。

## pip 包

`tpu_mlir[onnx]` 仍由服务器 pip 安装。若 pip 也受限，可在本机 `pip download tpu_mlir[onnx] -d deploy/offline/wheels/` 后 scp 到服务器并用 `pip install --no-index -f wheels`（需另配文档）。
