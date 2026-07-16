def test_dry_run_commit_requires_data_task_pool(client, auth_headers):
    response = client.post(
        "/api/v2/pipelines/pipeline-id/dry-run/dry-run-id/commit",
        headers=auth_headers,
    )

    assert response.status_code == 409
    assert "数据任务池" in response.json()["detail"]
