"""Command-line wrapper for the dedicated 7GAI Provider worker."""

from labelu.internal.application.service.provider_worker_runtime import main

if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
