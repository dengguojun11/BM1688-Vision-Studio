import json

from labelu.internal.application.service.training_review_gate import (
    filter_trainable_samples,
)
from labelu.internal.domain.models.sample import SampleState, TaskSample
from labelu.internal.domain.models.task import MediaType, Task


def test_third_party_done_sample_without_human_edit_is_not_trainable(db):
    task = Task(
        name="third-party",
        media_type=MediaType.IMAGE.value,
        source_type="third_party",
    )
    sample = TaskSample(
        task=task,
        data=json.dumps({"result": {"rectTool": {"result": []}}}),
        state=SampleState.DONE.value,
    )
    db.add(task)
    db.add(sample)
    db.flush()

    assert filter_trainable_samples(db, task=task, samples=[sample]) == []


def test_internal_done_sample_does_not_need_human_review(db):
    task = Task(name="internal", media_type=MediaType.IMAGE.value)
    sample = TaskSample(task=task, state=SampleState.DONE.value, data="{}")
    db.add(task)
    db.add(sample)
    db.flush()

    assert filter_trainable_samples(db, task=task, samples=[sample]) == [sample]
