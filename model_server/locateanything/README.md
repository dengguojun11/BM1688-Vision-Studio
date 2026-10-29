# LocateAnything-3B Model Server

LabelU 自动标注协议适配器，用于把 NVIDIA LocateAnything-3B 的 `<box><x1><y1><x2><y2></box>` 归一化坐标输出转换为 `rectTool` 预标注。

## 服务器启动

服务器已安装 LocateAnything 到 `/data/ai/locateanything-3b` 时：

```bash
cd /data/labeIU/labeIuproject/labeIU/model_server/locateanything
LA_QUANTIZATION=q4 \
LA_GPU_IDLE_UNLOAD_SECONDS=300 \
/data/ai/locateanything-3b/venv/bin/python server.py \
  --host 0.0.0.0 \
  --port 5001 \
  --install-root /data/ai/locateanything-3b \
  --model /data/ai/locateanything-3b/models/LocateAnything-3B \
  --quantization q4
```

默认使用 **Q4 / bitsandbytes NF4** 加载 LocateAnything，以降低显存占用。
如需临时退回原始精度，可设置 `LA_QUANTIZATION=none` 或启动参数 `--quantization none`。

默认 **300 秒无预标注请求** 后自动卸载模型并释放 GPU 显存（环境变量 `LA_GPU_IDLE_UNLOAD_SECONDS`，`0` 表示永不卸载）。
首次请求会按需重新加载，响应会略慢。

生产 systemd 服务建议使用仓库脚本安装，默认不预加载模型：

```bash
cd /data/labeIU/labeIuproject/labeIU
sudo bash scripts/ops/install_locateanything_service.sh
```

如确实要启动即加载模型并常驻显存，可显式设置：

```bash
sudo env LABELU_LA_PRELOAD=1 LABELU_LA_GPU_IDLE_UNLOAD_SECONDS=0 LABELU_LA_QUANTIZATION=q4 \
  bash scripts/ops/install_locateanything_service.sh
```

手动释放：`curl -X POST http://127.0.0.1:5001/unload`

`5000` 当前被 `/data/blue/clock/virtual_clock.py` 占用，因此默认使用 `5001`。

## LabelU 配置

```env
AI_AUTO_LABEL_ENABLED=true
AI_PROVIDER=local_http
AI_MODEL_ENDPOINT=http://127.0.0.1:5001/
AI_MODEL_TIMEOUT_SECONDS=120
AI_MODEL_NAME=nvidia/LocateAnything-3B
```

## 能力边界

- 当前适配器只返回 `rectTool` 标框。
- `polygonTool`、`pointTool`、`lineTool` 会被忽略。
- 如果 LocateAnything 返回多个 `<box>`，会按任务约束 `max_results_per_label` 截断。
- 框会裁剪到图片边界，宽高为 0 的无效框会被丢弃。
