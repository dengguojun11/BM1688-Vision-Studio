"""批量作业冲突分组：全局标签与目标框可并行，同组串行。"""

from types import SimpleNamespace

from labelu.internal.application.service.task_background_jobs import (
    GROUP_AUTO,
    GROUP_GEOMETRY,
    GROUP_TAGS,
    GROUP_UNKNOWN,
    _conflicts,
    _job_group,
)


def _job(**kwargs):
    defaults = {"job_kind": "MANUAL_BATCH", "payload_json": None}
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def test_manual_auto_label_jobs_are_grouped_by_mode():
    assert _job_group("auto_label", _job(payload_json='{"mode": "TAGS_ONLY"}')) == GROUP_TAGS
    assert _job_group("auto_label", _job(payload_json='{"mode": "BOX_AND_TAGS"}')) == GROUP_GEOMETRY
    assert _job_group("auto_label", _job()) == GROUP_UNKNOWN
    assert (
        _job_group(
            "auto_label",
            _job(job_kind="AUTO_SAMPLE_PREANNOTATE", payload_json='{"sample_ids": [1]}'),
        )
        == GROUP_AUTO
    )


def test_other_jobs_use_their_own_group():
    assert _job_group("tagtool_preannotate", _job()) == GROUP_TAGS
    assert _job_group("yolo_preannotate", _job()) == GROUP_GEOMETRY
    assert _job_group("locateanything_preannotate", _job()) == GROUP_GEOMETRY
    assert _job_group("vlx_seek_preannotate", _job()) == GROUP_GEOMETRY


def test_only_same_group_conflicts():
    assert _conflicts(GROUP_TAGS, GROUP_TAGS) is True
    assert _conflicts(GROUP_GEOMETRY, GROUP_GEOMETRY) is True
    assert _conflicts(GROUP_TAGS, GROUP_GEOMETRY) is False
    assert _conflicts(GROUP_GEOMETRY, GROUP_TAGS) is False
    # The automatic worker keeps running independently and never blocks a
    # manual action; legacy jobs without a stored mode stay conservative.
    assert _conflicts(GROUP_TAGS, GROUP_AUTO) is False
    assert _conflicts(GROUP_GEOMETRY, GROUP_AUTO) is False
    assert _conflicts(GROUP_TAGS, GROUP_UNKNOWN) is True
