"""systemd entry point for provider preannotation worker."""

from labelu.internal.application.service.provider_preannotate_worker_runtime import main


if __name__ == "__main__":
    main()
