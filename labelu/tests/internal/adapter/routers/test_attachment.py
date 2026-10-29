from pathlib import Path
import re

from sqlalchemy.orm import Session
from fastapi.testclient import TestClient
from PIL import Image

from labelu.internal.common.config import settings
from labelu.internal.common.crypto import encrypt_value
from labelu.internal.common.db import begin_transaction
from labelu.internal.domain.models.task import Task
from labelu.internal.domain.models.attachment import TaskAttachment
from labelu.internal.domain.models.data_source import DataSource
from labelu.internal.adapter.persistence import crud_user
from labelu.internal.adapter.persistence import crud_task
from labelu.internal.adapter.persistence import crud_attachment
from labelu.internal.application.service import attachment as attachment_service
from labelu.internal.application.service.yolo_edge_validation_external_cleanup import (
    cleanup_stale_edge_validation_external_attachments,
)
from labelu.tests.utils.utils import empty_task_upload, get_test_user, random_lower_string


class TestClassTaskAttachmentRouter:
    def test_upload_file_successful(
        self, client: TestClient, testuser_token_headers: dict, db: Session
    ) -> None:

        # prepare data
        data = {
            "name": "task name",
            "description": "task description",
            "tips": "task tips",
        }
        
        task = client.post(
            f"{settings.API_V1_STR}/tasks", headers=testuser_token_headers, json=data
        )
        task_id = task.json()["data"]["id"]
        
        empty_task_upload(task_id, "test.png")

        # run
        with Path("labelu/tests/data/test.png").open(mode="rb") as f:
            png_res = client.post(
                f"{settings.API_V1_STR}/tasks/{task_id}/attachments",
                headers=testuser_token_headers,
                files={"file": ("test.png", f, "image/png")},
            )
        # check
        json = png_res.json()
        assert png_res.status_code == 201
        assert json["data"]["url"].startswith("http")
        assert "/tasks/attachment/" in json["data"]["url"]

        parts = json["data"]["url"].split("/")[-3:]
        assert Path(f"{settings.MEDIA_ROOT}").joinpath("/".join(parts)).exists()

    def test_upload_file_for_edge_validation_external_image_uses_dedicated_dir(
        self, client: TestClient, testuser_token_headers: dict, db: Session
    ) -> None:
        data = {
            "name": "task name",
            "description": "task description",
            "tips": "task tips",
        }
        task = client.post(
            f"{settings.API_V1_STR}/tasks", headers=testuser_token_headers, json=data
        )
        task_id = task.json()["data"]["id"]

        filename = "edge-validation-" + random_lower_string()[:8] + ".png"
        with Path("labelu/tests/data/test.png").open(mode="rb") as f:
            png_res = client.post(
                f"{settings.API_V1_STR}/tasks/{task_id}/attachments",
                headers=testuser_token_headers,
                data={"purpose": "edge_validation_external_image"},
                files={"file": (filename, f, "image/png")},
            )

        assert png_res.status_code == 201, png_res.text
        body = png_res.json()["data"]
        assert body["purpose"] == "edge_validation_external_image"
        assert "/edge_validation_external/" in body["url"]

        attachment = crud_attachment.get(db=db, attachment_id=body["id"])
        assert attachment is not None
        assert "edge_validation_external" in str(attachment.path)
        
        empty_task_upload(task_id, "test.jsonl")
    
        with Path("labelu/tests/data/test.jsonl").open(mode="rb") as f:
            jsonl_res = client.post(
                f"{settings.API_V1_STR}/tasks/{task_id}/attachments",
                headers=testuser_token_headers,
                files={"file": ("test.jsonl", f, "application/json")},
            )
            
        json = jsonl_res.json()
        assert jsonl_res.status_code == 201
        
        parts = json["data"]["url"].split("/")[-3:]
        assert Path(f"{settings.MEDIA_ROOT}").joinpath("/".join(parts)).exists()
        
    def test_file_exists(
        self, client: TestClient, testuser_token_headers: dict, db: Session
    ) -> None:
        # prepare data
        data = {
            "name": "task name",
            "description": "task description",
            "tips": "task tips",
        }
        task = client.post(
            f"{settings.API_V1_STR}/tasks", headers=testuser_token_headers, json=data
        )
        task_id = task.json()["data"]["id"]
        filename = f"duplicate-{random_lower_string()[:8]}.png"

        # upload file twice with the same filename to verify duplicate rejection
        with Path("labelu/tests/data/test.png").open(mode="rb") as f:
            first = client.post(
                f"{settings.API_V1_STR}/tasks/{task_id}/attachments",
                headers=testuser_token_headers,
                files={"file": (filename, f, "image/png")},
            )
        assert first.status_code == 201

        with Path("labelu/tests/data/test.png").open(mode="rb") as f:
            second = client.post(
                f"{settings.API_V1_STR}/tasks/{task_id}/attachments",
                headers=testuser_token_headers,
                files={"file": (filename, f, "image/png")},
            )

        assert second.status_code == 400

    def test_upload_file_when_task_finished(
        self, client: TestClient, testuser_token_headers: dict, db: Session
    ) -> None:

        # prepare data
        current_user = get_test_user(db)
        with begin_transaction(db):
            task = crud_task.create(
                db=db,
                task=Task(
                    name="name",
                    description="description",
                    tips="tips",
                    config="config",
                    media_type="IMAGE",
                    status="FINISHED",
                    created_by=current_user.id,
                    updated_by=current_user.id,
                ),
            )

        empty_task_upload(task.id, "test.png")
        # run
        with Path("labelu/tests/data/test.png").open(mode="rb") as f:
            new_res = client.post(
                f"{settings.API_V1_STR}/tasks/{task.id}/attachments",
                headers=testuser_token_headers,
                files={"file": f},
            )

        # check
        assert new_res.status_code == 201

    def test_upload_file_not_image(
        self, client: TestClient, testuser_token_headers: dict, db: Session
    ) -> None:

        # prepare data
        data = {
            "name": "task name",
            "description": "task description",
            "tips": "task tips",
        }
        task = client.post(
            f"{settings.API_V1_STR}/tasks", headers=testuser_token_headers, json=data
        )
        task_id = task.json()["data"]["id"]
        
        empty_task_upload(task_id, "test.txt")

        # run
        with Path("labelu/tests/data/test.txt").open(mode="rb") as f:
            new_res = client.post(
                f"{settings.API_V1_STR}/tasks/{task_id}/attachments",
                headers=testuser_token_headers,
                files={"file": ("test.txt", f, "plain/text")},
            )

        # check
        json = new_res.json()
        assert new_res.status_code == 201

        parts = json["data"]["url"].split("/")[-3:]
        assert Path(f"{settings.MEDIA_ROOT}").joinpath("/".join(parts)).exists()
        parts[-1] = parts[-1][:8] + "-test-thumbnail.txt"
        assert not Path(f"{settings.MEDIA_ROOT}").joinpath("/".join(parts)).exists()

    def test_upload_file_when_task_not_found(
        self, client: TestClient, testuser_token_headers: dict, db: Session
    ) -> None:

        # prepare data

        # run
        with Path("labelu/tests/data/test.png").open(mode="rb") as f:
            new_res = client.post(
                f"{settings.API_V1_STR}/tasks/0/attachments",
                headers=testuser_token_headers,
                files={"file": f},
            )

        # check
        assert new_res.status_code == 404
        assert new_res.json()["err_code"] == 50002

    def test_download_file_successful(
        self, client: TestClient, testuser_token_headers: dict, db: Session
    ) -> None:

        # prepare data
        data = {
            "name": "task name",
            "description": "task description",
            "tips": "task tips",
        }
        task = client.post(
            f"{settings.API_V1_STR}/tasks", headers=testuser_token_headers, json=data
        )
        task_id = task.json()["data"]["id"]
        
        empty_task_upload(task_id, "test.png")

        # upload file
        with Path("labelu/tests/data/test.png").open(mode="rb") as f:
            attachment = client.post(
                f"{settings.API_V1_STR}/tasks/{task_id}/attachments",
                headers=testuser_token_headers,
                files={"file": f},
            )
        url = attachment.json()["data"]["url"]

        # run
        r = client.get(
            url,
            headers=testuser_token_headers,
        )

        # check
        assert r.status_code == 200
        assert r.headers["Cache-Control"] == "public, max-age=31536000"

    def test_annotation_display_uses_1672_max_side_by_default(
        self, client: TestClient, testuser_token_headers: dict, tmp_path
    ) -> None:
        large_image_key = "upload/display-default/large.jpg"
        large_image_path = Path(settings.MEDIA_ROOT) / large_image_key
        large_image_path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (2000, 1125), "white").save(large_image_path, format="JPEG")

        try:
            response = client.get(
                f"{settings.API_V1_STR}/tasks/annotation-display/{large_image_key}",
                headers=testuser_token_headers,
            )

            assert response.status_code == 200
            display_path = tmp_path / "display.jpg"
            display_path.write_bytes(response.content)
            with Image.open(display_path) as image:
                assert image.size == (1672, 941)
        finally:
            large_image_path.unlink(missing_ok=True)

    def test_download_file_not_found(
        self, client: TestClient, testuser_token_headers: dict, db: Session
    ) -> None:

        # run
        r = client.get(
            url=f"{settings.SCHEME}://{settings.HOST}:{settings.PORT}{settings.API_V1_STR}/tasks/attachment/upload/1/0",
            headers=testuser_token_headers,
        )

        # check
        assert r.status_code == 404
        assert r.json()["err_code"] == 51001

    def test_download_external_datasource_attachment_successful(
        self, client: TestClient, testuser_token_headers: dict, db: Session, tmp_path, monkeypatch
    ) -> None:
        current_user = get_test_user(db)
        with begin_transaction(db):
            task = crud_task.create(
                db=db,
                task=Task(
                    name="external datasource task",
                    description="description",
                    tips="tips",
                    created_by=current_user.id,
                    updated_by=current_user.id,
                ),
            )
            ds = DataSource(
                name="s3-proxy-test",
                type="S3",
                endpoint="http://example-minio:9000",
                bucket="bucket-a",
                prefix="",
                access_key_id=encrypt_value("ak"),
                secret_access_key=encrypt_value("sk"),
                path_style=True,
                use_ssl=False,
                presign_expire_secs=3600,
                created_by=current_user.id,
                updated_by=current_user.id,
            )
            db.add(ds)
            db.flush()
            attachment = crud_attachment.create(
                db=db,
                attachment=TaskAttachment(
                    task_id=task.id,
                    data_source_id=ds.id,
                    path="dataset/images/example.jpg",
                    url="",
                    filename="example.jpg",
                    created_by=current_user.id,
                    updated_by=current_user.id,
                ),
            )

        image_path = tmp_path / "external-example.jpg"
        Image.new("RGB", (32, 24), "white").save(image_path, format="JPEG")

        async def _fake_download_datasource_attachment(db: Session, attachment_id: int) -> dict:
            assert attachment_id == attachment.id
            return {"local_path": str(image_path), "content_type": "image/jpeg"}

        monkeypatch.setattr(
            attachment_service,
            "download_datasource_attachment",
            _fake_download_datasource_attachment,
        )

        response = client.get(
            f"{settings.API_V1_STR}/tasks/attachment/data-source/{attachment.id}",
            headers=testuser_token_headers,
        )

        assert response.status_code == 200
        assert response.headers["Cache-Control"] == "public, max-age=31536000"

    def test_task_delete_task_file(
        self, client: TestClient, testuser_token_headers: dict, db: Session
    ) -> None:

        # prepare data
        current_user = crud_user.get_user_by_username(
            db=db, username="test@example.com"
        )
        with begin_transaction(db):
            task = crud_task.create(
                db=db,
                task=Task(
                    name="name",
                    description="description",
                    tips="tips",
                    created_by=current_user.id,
                    updated_by=current_user.id,
                ),
            )
        
        empty_task_upload(task.id, "test.png")
        
        # upload file
        with Path("labelu/tests/data/test.png").open(mode="rb") as f:
            attachment = client.post(
                f"{settings.API_V1_STR}/tasks/{task.id}/attachments",
                headers=testuser_token_headers,
                files={"file": f},
            )
        attachment_id = attachment.json()["data"]["id"]
        attachment_path = attachment.json()["data"]["url"]

        # run
        data = {"attachment_ids": [attachment_id]}
        r = client.request("delete",
            f"{settings.API_V1_STR}/tasks/{task.id}/attachments",
            headers=testuser_token_headers,
            json=data,
        )

        # check
        deleted = crud_attachment.get(db=db, attachment_id=attachment_id)
        file_full_path = Path(settings.MEDIA_ROOT).joinpath(attachment_path)
        assert r.status_code == 200
        assert not deleted
        assert not file_full_path.exists()

    def test_task_delete_edge_validation_external_file_delays_physical_cleanup(
        self, client: TestClient, testuser_token_headers: dict, db: Session
    ) -> None:
        current_user = crud_user.get_user_by_username(
            db=db, username="test@example.com"
        )
        with begin_transaction(db):
            task = crud_task.create(
                db=db,
                task=Task(
                    name="edge external cleanup task",
                    description="description",
                    tips="tips",
                    created_by=current_user.id,
                    updated_by=current_user.id,
                ),
            )

        filename = "edge-cleanup-" + random_lower_string()[:8] + ".png"
        with Path("labelu/tests/data/test.png").open(mode="rb") as f:
            attachment = client.post(
                f"{settings.API_V1_STR}/tasks/{task.id}/attachments",
                headers=testuser_token_headers,
                data={"purpose": "edge_validation_external_image"},
                files={"file": (filename, f, "image/png")},
            )
        assert attachment.status_code == 201, attachment.text

        attachment_id = attachment.json()["data"]["id"]
        attachment_url = attachment.json()["data"]["url"]
        rel = "/".join(attachment_url.split("/")[-4:])
        full_path = Path(settings.MEDIA_ROOT).joinpath(rel)
        assert full_path.exists()

        r = client.request(
            "delete",
            f"{settings.API_V1_STR}/tasks/{task.id}/attachments",
            headers=testuser_token_headers,
            json={"attachment_ids": [attachment_id]},
        )
        assert r.status_code == 200

        deleted = crud_attachment.get(db=db, attachment_id=attachment_id)
        assert deleted is None
        raw = db.query(TaskAttachment).filter(TaskAttachment.id == attachment_id).first()
        assert raw is not None
        assert raw.deleted_at is not None
        assert full_path.exists()

    def test_cleanup_stale_edge_validation_external_soft_deleted_files(
        self, client: TestClient, testuser_token_headers: dict, db: Session, monkeypatch
    ) -> None:
        task_resp = client.post(
            f"{settings.API_V1_STR}/tasks",
            headers=testuser_token_headers,
            json={
                "name": "edge external cleanup task 2",
                "description": "description",
                "tips": "tips",
            },
        )
        assert task_resp.status_code == 201, task_resp.text
        task_id = task_resp.json()["data"]["id"]

        filename = "edge-cleanup-run-" + random_lower_string()[:8] + ".png"
        with Path("labelu/tests/data/test.png").open(mode="rb") as f:
            attachment = client.post(
                f"{settings.API_V1_STR}/tasks/{task_id}/attachments",
                headers=testuser_token_headers,
                data={"purpose": "edge_validation_external_image"},
                files={"file": (filename, f, "image/png")},
            )
        assert attachment.status_code == 201, attachment.text
        attachment_id = attachment.json()["data"]["id"]
        attachment_url = attachment.json()["data"]["url"]
        rel = "/".join(attachment_url.split("/")[-4:])
        full_path = Path(settings.MEDIA_ROOT).joinpath(rel)
        thumb_path = full_path.with_name(f"{full_path.stem}-thumbnail{full_path.suffix}")
        assert full_path.exists()
        assert thumb_path.exists()

        r = client.request(
            "delete",
            f"{settings.API_V1_STR}/tasks/{task_id}/attachments",
            headers=testuser_token_headers,
            json={"attachment_ids": [attachment_id]},
        )
        assert r.status_code == 200

        monkeypatch.setattr(
            settings, "YOLO_EDGE_VALIDATION_EXTERNAL_CLEANUP_ENABLED", True
        )
        monkeypatch.setattr(
            settings, "YOLO_EDGE_VALIDATION_EXTERNAL_RETENTION_HOURS", 1
        )
        summary = cleanup_stale_edge_validation_external_attachments()
        assert summary["soft_deleted_rows_removed"] == 0

        import datetime as _dt

        stale_deleted_at = _dt.datetime.now() - _dt.timedelta(hours=2)
        with begin_transaction(db):
            raw = db.query(TaskAttachment).filter(TaskAttachment.id == attachment_id).first()
            raw.deleted_at = stale_deleted_at
        summary = cleanup_stale_edge_validation_external_attachments()
        assert summary["soft_deleted_rows_removed"] == 1
        assert not full_path.exists()
        assert not thumb_path.exists()

    def test_task_delete_task_file_not_found_task(
        self, client: TestClient, testuser_token_headers: dict, db: Session
    ) -> None:

        # prepare data

        # run
        data = {"attachment_ids": [1]}
        r = client.request("delete",
            f"{settings.API_V1_STR}/tasks/0/attachments",
            headers=testuser_token_headers,
            json=data,
        )

        # check
        assert r.status_code == 404
        assert r.json()["err_code"] == 51001

    def test_task_delete_task_file_not_owner(
        self, client: TestClient, testuser_token_headers: dict, db: Session
    ) -> None:

        # prepare data
        with begin_transaction(db):
            task = crud_task.create(
                db=db,
                task=Task(
                    name="name",
                    description="description",
                    tips="tips",
                    created_by=0,
                    updated_by=0,
                ),
            )

        # run
        data = {"attachment_ids": [1]}
        r = client.request("delete",
            f"{settings.API_V1_STR}/tasks/{task.id}/attachments",
            headers=testuser_token_headers,
            json=data,
        )

        # check
        assert r.status_code == 403
        assert r.json()["err_code"] == 30001

    def test_partial_content(
        self, client: TestClient, testuser_token_headers: dict, db: Session
    ) -> None:
        # prepare data
        data = {
            "name": "partial content task",
            "description": "task description",
            "tips": "task tips",
        }
        task = client.post(
            f"{settings.API_V1_STR}/tasks", headers=testuser_token_headers, json=data
        )
        task_id = task.json()["data"]["id"]
        
        empty_task_upload(task_id, "test.txt")

        # 上传测试文件
        with Path("labelu/tests/data/test.txt").open(mode="rb") as f:
            attachment = client.post(
                f"{settings.API_V1_STR}/tasks/{task_id}/attachments",
                headers=testuser_token_headers,
                files={"file": ("test.txt", f, "text/plain")},
            )
        url = attachment.json()["data"]["url"]
        
        # 转换为 partial URL
        partial_url = url.replace("/attachment/", "/partial/")
        
        # 测试 Range 请求
        r1 = client.get(
            partial_url,
            headers={**testuser_token_headers, "Range": "bytes=0-9"},
        )
        
        # 检查
        assert r1.status_code == 206  # 部分内容响应码
        assert r1.headers.get("Content-Length") == "10"
        assert r1.headers.get("Content-Range").startswith("bytes 0-9/")
        
        # 测试没有 Range 的请求
        r2 = client.get(
            partial_url,
            headers=testuser_token_headers,
        )
        
        # 检查
        assert r2.status_code == 200
        assert "Accept-Ranges" in r2.headers
        
        # 测试无效的 Range 请求
        r3 = client.get(
            partial_url,
            headers={**testuser_token_headers, "Range": "invalid"},
        )
        
        # 检查 - 应该回退到完整响应
        assert r3.status_code == 200
        
        # 测试超出文件范围的请求
        with Path("labelu/tests/data/test.txt").open(mode="rb") as f:
            file_size = len(f.read())
            
        r4 = client.get(
            partial_url,
            headers={**testuser_token_headers, "Range": f"bytes={file_size + 10}-{file_size + 20}"},
        )
        
        # 检查 - 应该回退到完整响应
        assert r4.status_code == 200

    def test_partial_content_file_not_found(
        self, client: TestClient, testuser_token_headers: dict, db: Session
    ) -> None:
        # 测试不存在的文件
        r = client.get(
            f"{settings.API_V1_STR}/partial/non-existent/file.txt",
            headers=testuser_token_headers,
        )
        
        # 检查
        assert r.status_code == 404
        assert r.json()["err_code"] == 30003

    def test_upload_file_with_hash_symbol(
        self, client: TestClient, testuser_token_headers: dict, db: Session
    ) -> None:
        # prepare data（任务名加随机后缀，避免全量 pytest 时与其它用例撞库/目录）
        data = {
            "name": "hash symbol test " + random_lower_string(),
            "description": "test for files with # in name",
            "tips": "test tips",
        }
        task = client.post(
            f"{settings.API_V1_STR}/tasks", headers=testuser_token_headers, json=data
        )
        task_id = task.json()["data"]["id"]

        # 文件名含随机段，避免全量 pytest 复用 task_id=1 时磁盘上残留同名附件
        tag = random_lower_string()[:8]
        original_filename = f"test#{tag}#data.txt"
        expected_filename = f"test_{tag}_data.txt"
        empty_task_upload(task_id, expected_filename)

        # 打开测试文件并上传（使用带有井号的文件名）
        with Path("labelu/tests/data/test.txt").open(mode="rb") as f:
            response = client.post(
                f"{settings.API_V1_STR}/tasks/{task_id}/attachments",
                headers=testuser_token_headers,
                files={"file": (original_filename, f, "text/plain")},
            )

        # 检查
        json = response.json()
        assert response.status_code == 201

        # 验证文件名中的井号被替换为下划线
        assert "#" not in json["data"]["filename"]
        assert expected_filename == json["data"]["filename"]
        
        # 验证文件实际存在于文件系统
        parts = json["data"]["url"].split("/")[-3:]
        assert Path(f"{settings.MEDIA_ROOT}").joinpath("/".join(parts)).exists()


class TestAttachmentUtils:
    def test_external_datasource_attachment_uses_backend_proxy_urls(
        self, db: Session
    ) -> None:
        current_user = get_test_user(db)
        with begin_transaction(db):
            task = crud_task.create(
                db=db,
                task=Task(
                    name="proxy url task",
                    description="description",
                    tips="tips",
                    created_by=current_user.id,
                    updated_by=current_user.id,
                ),
            )
            ds = DataSource(
                name="proxy-datasource",
                type="S3",
                endpoint="http://example-minio:9000",
                bucket="bucket-a",
                prefix="",
                access_key_id=encrypt_value("ak"),
                secret_access_key=encrypt_value("sk"),
                path_style=True,
                use_ssl=False,
                presign_expire_secs=3600,
                created_by=current_user.id,
                updated_by=current_user.id,
            )
            db.add(ds)
            db.flush()
            attachment = crud_attachment.create(
                db=db,
                attachment=TaskAttachment(
                    task_id=task.id,
                    data_source_id=ds.id,
                    path="dataset/images/example.jpg",
                    url="",
                    filename="example.jpg",
                    created_by=current_user.id,
                    updated_by=current_user.id,
                ),
            )

        response = attachment_service.build_attachment_response(
            attachment, for_annotation=True
        )

        assert response is not None
        assert response.url.endswith(
            f"{settings.API_V1_STR}/tasks/attachment/data-source/{attachment.id}"
        )
        assert response.stream_url.endswith(
            f"{settings.API_V1_STR}/tasks/partial/data-source/{attachment.id}"
        )
        assert response.annotation_display_url.endswith(
            f"{settings.API_V1_STR}/tasks/annotation-display/data-source/{attachment.id}"
        )

    def test_filename_sanitization(self):
        """测试文件名清理功能，验证井号(#)是否被正确处理"""
        # 模拟 attachment.py 中的文件名清理逻辑
        original_filename = "test#file#with#hash.txt"
        sanitized = re.sub(r'%', '_pct_', original_filename)
        sanitized = re.sub(r'[\\/*?:"<>|#]', '_', sanitized)
        
        # 验证井号被替换为下划线
        assert "#" not in sanitized
        assert "test_file_with_hash.txt" == sanitized
        
        # 测试其他特殊字符也能被正确处理
        complex_filename = r"file\with/special*chars?:and\"<>|#symbols.txt"
        sanitized = re.sub(r'%', '_pct_', complex_filename)
        sanitized = re.sub(r'[\\/*?:"<>|#]', '_', sanitized)
        
        # 检查正确结果（拆分为多个断言便于排除问题）
        assert sanitized.startswith("file_with_special_chars_")
        assert sanitized.endswith("_symbols.txt")
        assert "and" in sanitized
        # 直接检查整个转义序列被正确处理
        assert "\\" not in sanitized
