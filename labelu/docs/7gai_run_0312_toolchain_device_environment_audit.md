# RUN-0312 工具链与真实设备入口环境核验（2026-09-20）

## 1. 本轮目标

按角色团队的 R06 模型算法、R07 BM1688 边端、R09 测试和 R10 运维边界，核对本机和已登记环境是否具备真实训练、TPU-MLIR 编译和 BM1688 真机验证的执行前提，并为后续 L2/L3 验收建立可复核证据。

本轮是只读环境审计，不修改业务代码、数据库、运行开关或生产配置。

## 2. 涉及菜单和按钮

- **训练中心 / 创建训练作业**：确认真实 GPU 训练节点入口是否存在。
- **模型中心 / 边端编译**：确认服务器 TPU-MLIR 和 Sophon 工具是否可执行。
- **模型中心 / 真机验证**：确认 BM1688 SSH 或板端主动拉取入口是否可达。
- **系统管理 / 设备、运行开关**：确认当前仍应保持失败关闭。

## 3. 角色分工

| 角色 | 本轮职责 | 结果 |
|---|---|---|
| R03 架构/迁移 | 核对工具链、设备入口与现有配置契约 | 已完成代码/配置只读核对 |
| R06 模型算法 | 核对 PT→静态 ONNX→三精度 BModel 的宿主机前置条件 | 工具未在本机安装，真实编译未执行 |
| R07 BM1688 边端 | 核对 SSH/Board Agent/容器入口 | 已登记入口，但本机 SSH 不可达 |
| R09 测试 | 固化命令、输出和不可替代证据边界 | 已完成 |
| R10 运维/生产 | 提供可达主机、服务账号、systemd 和设备证据 | 尚未提供/尚未可达 |

## 4. 本机只读核验

执行命令：

```text
Get-Command python,nvidia-smi,docker,ssh,scp,tpu_mlir,model_deploy,bmrt_test,mlir_shell,mysql,mysqld
```

结果：

| 能力 | 本机结果 | 结论 |
|---|---|---|
| Python | `D:\Python311\python.exe` | 可运行本地测试和服务代码 |
| NVIDIA GPU 工具 | `C:\Windows\system32\nvidia-smi.exe` | 仅证明工具存在，不证明训练 GPU 可用 |
| Docker | `C:\Program Files\Docker\Docker\resources\bin\docker.exe` | 仅证明客户端存在，不证明 TPU 镜像和设备可用 |
| SSH/SCP | Windows OpenSSH | 可发起远程探测 |
| TPU-MLIR | 未找到 `tpu_mlir` | 本机不能宣称真实编译 |
| `model_deploy` | 未找到 | 本机不能导出 BModel |
| `bmrt_test` | 未找到 | 本机不能做 BM1688 加载/推理验证 |
| `mlir_shell` | 未找到 | 本机不能完成 TPU-MLIR 编译链 |
| MySQL 客户端/服务端 | `mysql`、`mysqld` 均未找到 | 本机不能代替同版本 MySQL 验收 |

因此，本机目前只能执行代码、契约、模拟 Worker 和 SQLite 测试，不能把本地测试结果标为 TPU-MLIR、GPU 训练、BM1688 或 MySQL 生产证据。

## 5. 已登记远程入口（只读配置核对）

配置文件 `config/env/.env.server` 登记了以下入口：

| 入口 | 配置值/状态 | 允许的判断 |
|---|---|---|
| TPU-MLIR 根目录 | `/data/labeIU/tpu-mlir` | 这是目标路径，不是已存在证明 |
| BM1688 SSH 主机 | `10.10.11.26:22`，用户 `linaro` | 这是登记地址，不是在线证明 |
| BM1688 远程目录 | `/data/labelu_edge_eval` | 只能作为目标工作目录 |
| Sophon 示例目录 | `/home/linaro/sophon-work/sophon-demo/sample/YOLOv8_plus_det` | 只能作为目标工具目录 |
| BM1688 容器 | `bm1688`，通过远端 Docker 执行 | 需要设备在线、容器存在且工具可用 |
| Board Agent 公网回连地址 | `http://113.141.72.175:10000` | 仅为回连配置，不能替代心跳、签名和回传证据 |
| GPU 训练节点 | 由 `remote_train_nodes` 治理配置读取 | 当前工作树未发现可直接核验的真实节点清单/在线证明 |

配置中的凭据、令牌和密钥未读取、未输出、未写入文档。

## 6. 远程连通性探测

只读命令使用 `BatchMode=yes`、`ConnectTimeout=5`、`StrictHostKeyChecking=no`，未执行远程写入：

```text
ssh -o BatchMode=yes -o ConnectTimeout=5 linaro@10.10.11.26 "uname -a; command -v bmrt_test"
ssh -o BatchMode=yes -o ConnectTimeout=5 root@113.141.72.175 "hostname; command -v mysql"
```

结果：两条连接均为 `Connection timed out`。因此无法核验远端操作系统、TPU-MLIR、Sophon、`bmrt_test`、Docker 容器、MySQL 或 systemd 状态。该结果不是设备故障结论，只表示当前执行环境没有取得网络可达证据。

## 7. 对角色团队的放行结论

- **R06**：PT、静态 ONNX、FP32/FP16/INT8 BModel 的业务契约和本地代码已存在；真实三精度编译仍为未完成。
- **R07**：SSH 与 Board Agent 代码路径、签名、租约、fencing 和失败关闭已有本地契约；没有在线 BM1688 证据前，不得启用真机 Worker。
- **R09**：本轮环境审计可复核，但不能用 `nvidia-smi` 存在、Docker 客户端存在或 HTTP 200 代替真实设备验收。
- **R10**：需要提供可达的 GPU 训练节点、TPU-MLIR 编译节点、BM1688 设备或板端主动拉取入口，并提供脱敏的工具版本、设备能力、systemd、磁盘/容器和短稳日志。

以下开关继续保持关闭或失败关闭：

```text
YOLO_BM1688_DEDICATED_WORKER_ENABLED=false
YOLO_EDGE_VALIDATION_DEDICATED_WORKER_ENABLED=false
LABELU_ENABLE_EDGE_VALIDATION_WORKER=0
LABELU_ENABLE_BM1688_WORKER=0
BOARD_AGENT_PRIMARY_ENABLED=false
EXPERIMENT_DATASET_DEDICATED_WORKER_ENABLED=false
```

## 8. 下一步真实证据清单

1. 运维提供 GPU 节点和 TPU-MLIR 节点的可达入口、同版本 Python/驱动/工具版本和最小权限账号。
2. 在编译节点完成一个真实 YOLO/车牌矩形框 PT 的静态 ONNX 导出，并保存 ONNX 校验值。
3. 在同一编译节点生成 FP32、FP16、INT8 三种 BModel，保存 TPU-MLIR/Sophon 版本、校准集摘要和制品校验值。
4. 在真实 BM1688 上完成三精度加载、有效推理数、P95、短稳、断网/重启接管和资源释放证据。
5. 在同版本 MySQL 沙箱完成迁移重复升级、备份恢复、并发领取和旧 fencing token 拒绝证据。
6. 证据完成后再进入 L2/L3 放行评审；在此之前禁止开启生产 Worker、禁止生产 DDL、禁止对外发布真实模型。

## 9. 验收等级与状态

- 本轮等级：**L0（环境与代码路径审计）**。
- 代码变更：无。
- 数据库变更：无。
- 生产写入/部署：无。
- 真实 TPU-MLIR、GPU、BM1688、AICM、S3、MySQL、systemd：未完成。
- 是否允许提交/部署：不允许；当前工作树继续等待用户明确“提交代码”，且真实环境证据仍是前置条件。
