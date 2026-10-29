def test_project_ownership_audit_is_admin_only(
    client, testadmin_token_headers, testuser_token_headers
):
    admin_response = client.get(
        "/api/v1/project-ownership/audit?include_ids=true&sample_limit=5",
        headers=testadmin_token_headers,
    )
    assert admin_response.status_code == 200
    payload = admin_response.json()["data"]
    assert payload["read_only"] is True
    assert payload["policy"]["allow_owner_inference"] is False

    user_response = client.get(
        "/api/v1/project-ownership/audit", headers=testuser_token_headers
    )
    assert user_response.status_code == 403
