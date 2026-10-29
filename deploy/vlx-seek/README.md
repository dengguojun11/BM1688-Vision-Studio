# VLX-Seek 1.5-10B WebUI deployment

This directory tracks the production overlay for the standalone VLX-Seek visual perception platform at `https://10.10.11.27:8099/`. It is separate from the LabelU FastAPI service and database. The local Windows development profile uses the same model service address.

## Pinned upstream and external assets

- Source: `https://github.com/om-ai-lab/VLX-Seek.git`
- Pinned revision: `7f0e8690be3af866c0cde42d13dc906c917ab25f`
- Model: `omlab/VLX-Seek-1.5-10B`
- Proposal model: `fushh7/WeDetect/wedetect_base_uni.pth`

Model weights and the WeDetect checkpoint are intentionally not stored in Git. The deployed paths are:

- Source: `/home/ubuntu/vlx-seek-deploy/VLX-Seek`
- Model: `/home/ubuntu/vlx-seek-deploy/models/VLX-Seek-1.5-10B`
- WeDetect: `/home/ubuntu/vlx-seek-deploy/VLX-Seek/resources/wedetect_base_uni.pth`
- Service: `/etc/systemd/system/vlx-seek.service`

## What the overlay changes

The upstream inference code remains the baseline. Files under `runtime/` provide:

- a FastAPI image upload and inference API;
- a Chinese/English WebUI for all seven official task templates;
- annotated result images with stable per-label colors and CJK fonts;
- balanced placement across two NVIDIA GPUs;
- SDPA attention for the installed PyTorch/Transformers runtime;
- correct device placement for the auxiliary vision tower.

The supported tasks are VQA, open-vocabulary detection, single-object grounding, counting, reasoning detection, region captioning, and region OCR.

## Redeploy an existing installation

Upload this directory to the server and run:

```bash
sudo env LABELU_DEPLOY_COMMIT=<git-commit> bash deploy/vlx-seek/deploy.sh
```

The script validates that the official source, model, detector, and Python runtime already exist. It then installs only the tracked overlay, compiles the Python entry points, installs the systemd unit, enables the service, and restarts it.

## Verification

```bash
systemctl is-active vlx-seek.service
systemctl is-enabled vlx-seek.service
curl -ks https://127.0.0.1:8099/api/health
nvidia-smi --query-compute-apps=pid,process_name,used_memory --format=csv
```

`/api/health` is ready only when `phase` is `ready`. A successful deployment also requires one real multipart `/api/infer` request; an HTTP 200 page alone does not prove that model inference works.
