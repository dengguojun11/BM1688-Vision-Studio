from labelu.internal.adapter.persistence import crud_task, crud_yolo_train_job
from labelu.internal.domain.models.project import Project, ProjectMember
from labelu.internal.domain.models.task import MediaType, Task
from labelu.internal.domain.models.yolo_train_job import YoloTrainJob
from labelu.internal.common.db import begin_transaction
from labelu.internal.application.service.auto_training_plan import _assert_manage, _assert_visible
from labelu.internal.domain.models.auto_training import AutoTrainingPlan
from labelu.internal.domain.models.user import User


def _seed_project_task_job(db, *, user_id: int, suffix: str):
    project = Project(
        owner_id=user_id,
        name=f"project-{suffix}",
        status="ACTIVE",
        created_by=user_id,
        updated_by=user_id,
    )
    db.add(project)
    db.flush()
    db.add(ProjectMember(project_id=project.id, user_id=user_id, role="owner", status="ACTIVE"))
    task = crud_task.create(
        db,
        Task(
            name=f"task-{suffix}",
            media_type=MediaType.IMAGE.value,
            status="FINISHED",
            created_by=user_id,
            updated_by=user_id,
            project_id=project.id,
        ),
    )
    job = YoloTrainJob(
        task_id=task.id,
        project_id=project.id,
        created_by=user_id,
        status="COMPLETED",
        weights_path=f"weights-{suffix}.pt",
    )
    db.add(job)
    db.flush()
    return project, task, job


def test_task_and_training_job_queries_respect_project_filter(db):
    with begin_transaction(db):
        first = _seed_project_task_job(db, user_id=1, suffix="a")
        second = _seed_project_task_job(db, user_id=1, suffix="b")

    tasks, total = crud_task.list_by(db, owner_id=1, project_id=first[0].id)
    assert total == 1
    assert [row.id for row in tasks] == [first[1].id]

    jobs, total = crud_yolo_train_job.list_jobs_for_user(
        db, user_id=1, project_id=second[0].id, task_access_unrestricted=True
    )
    assert total == 1
    assert [row[0].id for row in jobs] == [second[2].id]


def test_auto_training_plan_visibility_uses_project_membership(db):
    owner = db.query(User).filter(User.id == 1).one()
    member = User(
        username="plan-member@example.com",
        hashed_password="test",
        account_type="human",
        is_service_account=False,
        interactive_login_enabled=True,
    )
    db.add(member)
    db.flush()
    with begin_transaction(db):
        project = Project(
            owner_id=owner.id,
            name="plan-visibility",
            status="ACTIVE",
            created_by=owner.id,
            updated_by=owner.id,
        )
        db.add(project)
        db.flush()
        db.add(ProjectMember(project_id=project.id, user_id=owner.id, role="owner", status="ACTIVE"))
        db.add(ProjectMember(project_id=project.id, user_id=member.id, role="member", status="ACTIVE"))
        plan = AutoTrainingPlan(
            project_id=project.id,
            owner_id=owner.id,
            name="member-visible-plan",
            series_type="yolo_detection",
            dataset_category_id=1,
            label_set_signature="a" * 64,
            class_order_signature="b" * 64,
            status="DRAFT_INCOMPLETE",
            version=1,
            created_by=owner.id,
            updated_by=owner.id,
        )
        db.add(plan)
        db.flush()
    assert _assert_visible(db, plan, member).id == plan.id
    with pytest.raises(Exception):
        _assert_manage(db, plan, member)
    db.query(ProjectMember).filter_by(project_id=project.id, user_id=member.id).update({"role": "admin"})
    db.commit()
    assert _assert_manage(db, plan, member).id == plan.id
    db.query(ProjectMember).filter_by(project_id=project.id, user_id=member.id).update({"role": "member", "status": "DISABLED"})
    db.commit()
    with pytest.raises(Exception):
        _assert_visible(db, plan, member)
import pytest
