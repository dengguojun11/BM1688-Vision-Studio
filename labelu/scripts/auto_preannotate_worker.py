"""systemd entry point for the dedicated automatic preannotation worker."""

from labelu.internal.application.service.auto_preannotate_worker_runtime import main


if __name__ == "__main__":
    main()

