"""systemd entry point for the dedicated manual YOLO preannotation worker."""

from labelu.internal.application.service.yolo_preannotate_worker_runtime import main


if __name__ == "__main__":
    main()
