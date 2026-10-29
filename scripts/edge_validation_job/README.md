# BModel 模型验证包生成器

环境包只需安装一次。每次验证时，在 Windows 开发机上用本生成器把模型、标签信息和图片封装成一个数据包，再上传到边端 `/data`。

## 普通 YOLO

```powershell
python build_job.py `
  --model D:\models\model_fp16_1b.bmodel `
  --classes D:\models\classes.txt `
  --images D:\verify-images `
  --job-id test-001 `
  --output D:\output\bmodel-validation-job-test-001.tar.gz
```

## 车牌识别

```powershell
python build_job.py `
  --model D:\models\plate_recognition_fp16_1b.bmodel `
  --contract D:\models\plate_recognition_contract.json `
  --images D:\verify-images `
  --job-id plate-test-001 `
  --output D:\output\bmodel-validation-job-plate-test-001.tar.gz
```

上传后执行：

```bash
sudo bmodel-validate /data/bmodel-validation-job-test-001.tar.gz
```
