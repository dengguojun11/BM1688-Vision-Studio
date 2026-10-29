"""第三方 S3 问题数据回流 Worker 命令行入口。"""

from labelu.internal.application.service.experiment_dataset_import_worker import main


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

