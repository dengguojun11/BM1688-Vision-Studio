import json

from labelu.internal.adapter.persistence import crud_sample
from labelu.internal.domain.models.annotation_contract import SampleAnnotationLabelIndex
from labelu.internal.domain.models.sample import TaskSample


def _sample_data(*labels: str) -> str:
    return json.dumps(
        {
            "result": json.dumps(
                {
                    "rectTool": {
                        "result": [
                            {"id": str(index), "label": label}
                            for index, label in enumerate(labels)
                        ]
                    }
                }
            )
        }
    )


def test_crud_sample_keeps_distinct_rectangle_label_index_in_sync(db):
    sample = crud_sample.batch(
        db,
        [
            TaskSample(
                task_id=101,
                file_id=201,
                created_by=1,
                updated_by=1,
                data=_sample_data("vehicle", "vehicle", "person"),
            )
        ],
    )[0]

    rows = (
        db.query(SampleAnnotationLabelIndex)
        .filter(SampleAnnotationLabelIndex.sample_id == sample.id)
        .order_by(SampleAnnotationLabelIndex.label_value)
        .all()
    )
    assert [(row.task_id, row.label_value) for row in rows] == [
        (101, "person"),
        (101, "vehicle"),
    ]

    crud_sample.update(db, sample, {TaskSample.data.key: _sample_data("bicycle")})
    assert [
        row.label_value
        for row in db.query(SampleAnnotationLabelIndex)
        .filter(SampleAnnotationLabelIndex.sample_id == sample.id)
        .all()
    ] == ["bicycle"]

    crud_sample.delete(db, [sample.id])
    assert (
        db.query(SampleAnnotationLabelIndex)
        .filter(SampleAnnotationLabelIndex.sample_id == sample.id)
        .count()
        == 0
    )
