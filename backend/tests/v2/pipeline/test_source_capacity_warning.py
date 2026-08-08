"""源行数容量预警与生产硬拒绝的回归测试。

预警（新增行为）：生产环境下源行数超过 pipeline_max_in_memory_rows 的
pipeline_source_warn_ratio 比例时，run.stats 写入 source_warnings；
硬拒绝（既有行为）：超过上限直接失败。两者都只在生产环境生效。
"""
from app.config import settings
from app.data_channel.datasets.service import DatasetService, rows_to_parquet_bytes
from app.models.v2.pipeline import Pipeline, PipelineRun, PipelineVersion
from app.tasks.v2.pipeline_run import pipeline_run_task


class _Storage:
    def __init__(self):
        self.objects = {}

    def put_bytes(self, bucket, key, data, content_type=""):
        uri = f"s3://{bucket}/{key}"
        self.objects[uri] = data
        return uri

    def get_object(self, uri):
        return self.objects[uri]

    def delete_object(self, uri):
        self.objects.pop(uri, None)


def _run_with_source_rows(db, monkeypatch, rows: int, *, env: str, cap: int):
    from sqlalchemy.orm import sessionmaker

    svc = DatasetService(db, storage=_Storage())
    source_ds = svc.create_dataset(name=f"源表{rows}", kind="raw", schema_json={})
    svc.create_version(
        source_ds.id,
        rows_to_parquet_bytes([{"order_id": f"A-{i}"} for i in range(rows)]),
        rowcount=rows, schema_json={})
    pipe_id = f"pipe-cap-{rows}-{env}"
    run_id = f"run-cap-{rows}-{env}"
    db.add(Pipeline(
        id=pipe_id, name="容量流水线", spec={}, definition=None,
        source_dataset_id=source_ds.id, status="published", enabled=True,
        column_definitions=None))
    # 生产校验要求与 live 配置一致的发布快照（防漂移）
    db.add(PipelineVersion(
        id=f"{pipe_id}-v1", pipeline_id=pipe_id, version=1,
        definition=None, column_definitions=None, status="published"))
    db.add(PipelineRun(id=run_id, pipeline_id=pipe_id, status="pending"))
    db.commit()

    monkeypatch.setattr(settings, "environment", env)
    monkeypatch.setattr(settings, "pipeline_max_in_memory_rows", cap)
    runtime_session = sessionmaker(bind=db.get_bind())
    monkeypatch.setattr("app.database.SessionLocal", runtime_session)

    pipeline_run_task.run(pipe_id, run_id)

    run_db = runtime_session()
    try:
        return run_db.query(PipelineRun).filter(PipelineRun.id == run_id).one()
    finally:
        run_db.close()


def test_source_capacity_warning_emitted_near_limit(db, monkeypatch):
    run = _run_with_source_rows(db, monkeypatch, 9, env="production", cap=10)

    assert run.status == "success", run.error_log
    warnings = (run.stats or {}).get("source_warnings")
    assert warnings and len(warnings) == 1
    assert "9" in warnings[0] and "10" in warnings[0]
    assert "拒绝执行" in warnings[0]


def test_no_source_warning_below_threshold(db, monkeypatch):
    run = _run_with_source_rows(db, monkeypatch, 5, env="production", cap=10)

    assert run.status == "success", run.error_log
    assert (run.stats or {}).get("source_warnings") is None


def test_no_source_warning_outside_production(db, monkeypatch):
    run = _run_with_source_rows(db, monkeypatch, 9, env="development", cap=10)

    assert run.status == "success", run.error_log
    assert (run.stats or {}).get("source_warnings") is None


def test_source_over_limit_still_refused_in_production(db, monkeypatch):
    """既有硬拒绝行为保持不变：超过上限运行失败且不写资产湖。"""
    run = _run_with_source_rows(db, monkeypatch, 11, env="production", cap=10)

    assert run.status == "failed"
    assert "已拒绝执行" in (run.error_log or "")
