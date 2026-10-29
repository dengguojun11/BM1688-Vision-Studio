from types import SimpleNamespace

from labelu.internal.adapter.persistence import crud_yolo_preannotate_job


class _DbStub:
    def flush(self) -> None:
        pass

    def refresh(self, _job) -> None:
        pass


def _job():
    return SimpleNamespace(processed_count=0, success_count=0, failed_count=0)


def test_progress_counts_only_images_with_boxes_as_success() -> None:
    job = _job()
    db = _DbStub()

    crud_yolo_preannotate_job.increment_progress(
        db, job, success=True, annotated=True
    )
    crud_yolo_preannotate_job.increment_progress(
        db, job, success=True, annotated=False
    )
    crud_yolo_preannotate_job.increment_progress(db, job, success=False)

    assert job.processed_count == 3
    assert job.success_count == 1
    assert job.failed_count == 1
