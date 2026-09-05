"""run.stats 瘦身回归测试。

v2_pipeline_runs.stats 曾原样嵌入流水线定义/契约快照与 ctx.meta（含宽表
拆分的 split_tables 全量行列表），单行 JSON 可达百 MB 级。这些字段全库
零消费方。本文件锁定：写入侧不再产生这些键，消费方可见字段保持完整。
"""
from types import SimpleNamespace

from app.data_channel.datasets.service import DatasetService
from app.models.v2.pipeline import Pipeline, PipelineRun
from app.tasks.v2.pipeline_run import _save_curated_dataset, pipeline_run_task

_DEAD_STATS_KEYS = (
    "definition_snapshot",
    "spec_snapshot",
    "column_definitions_snapshot",
    "pipeline_version_snapshot_id",
)


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


def _pipeline(pid: str, name: str, targets=None):
    return SimpleNamespace(
        id=pid, name=name, target_curated_ids=targets or [], column_definitions=[])


def test_save_curated_dataset_slims_ctx_meta_in_output(db, monkeypatch):
    """ctx.meta 的全量负载不得随 output 字典进入 run.stats 透传链。"""
    from app.data_channel.datasets import service as dataset_service_module

    storage = _Storage()
    monkeypatch.setattr(
        dataset_service_module, "get_storage_service", lambda: storage)
    pl = _pipeline("pipe-slim", "订单")
    source = {"dataset_id": None, "filename": "orders", "route": "A"}
    ctx = SimpleNamespace(rows_in=2, meta={
        "rows_before": 2,
        "rows_after": 2,
        "split_tables": {"orders": [{"order_id": "A-1"}] * 1000},
        "inferred_schema": {"order_id": "string"},
    })
    svc = DatasetService(db, storage=storage)

    out = _save_curated_dataset(
        db, svc, pl, source, [{"order_id": "A-1", "amount": "10"}], ctx, False,
        write_opts={"mode": "overwrite", "skip_empty": False})

    assert out["meta"] == {"rows_before": 2, "rows_after": 2}
    # 执行记录消费方依赖的字段保持完整
    assert out["curated_dataset_id"]
    assert out["lake_impact"]["total_after"] == 1
    assert out["output_sample"][0]["order_id"] == "A-1"
    assert out["merge"]["mode"] == "overwrite"


def test_pipeline_run_task_stats_exclude_dead_fields(db, monkeypatch):
    """真实执行路径（python 引擎）落终态 stats：无快照死字段，outputs meta 已裁剪。"""
    from sqlalchemy.orm import sessionmaker

    from app.data_channel.pipelines.python_engine.client import ScriptExecution

    db.add(Pipeline(
        id="pipe-slim-run", name="订单流水线", spec={},
        definition={
            "engine": "python",
            "python": {"script": "result = [{'order_id': 'A-1', 'amount': '10'}]"},
        },
        status="published", enabled=True,
        column_definitions=None))
    db.add(PipelineRun(id="run-slim-1", pipeline_id="pipe-slim-run",
                       status="pending"))
    db.commit()

    runtime_session = sessionmaker(bind=db.get_bind())
    monkeypatch.setattr("app.database.SessionLocal", runtime_session)
    monkeypatch.setattr(
        "app.data_channel.pipelines.python_engine.runner.execute_script",
        lambda script, *, timeout, params=None: ScriptExecution(
            rows=[{"order_id": "A-1", "amount": "10"}],
            stdout="",
            error=None,
            traceback="",
            duration_ms=12,
            kernel_id="kernel-1",
        ))

    pipeline_run_task("pipe-slim-run", "run-slim-1")

    run_db = runtime_session()
    try:
        run = run_db.query(PipelineRun).filter(
            PipelineRun.id == "run-slim-1").one()
        assert run.status == "success", run.error_log
        stats = run.stats or {}
        for key in _DEAD_STATS_KEYS:
            assert key not in stats
        assert stats["pipeline_version"] == 1
        # 消费方可见的执行摘要保持完整
        assert stats["engine"] == "python"
        assert stats["rows_in"] == 1
        assert stats["rows_out"] == 1
        assert stats["lake_rows"] == 1
        assert stats["curated_dataset_ids"]
        outputs = stats["meta"]["outputs"]
        assert len(outputs) == 1
        meta_keys = set((outputs[0]["meta"] or {}).keys())
        assert meta_keys <= {"rows_before", "rows_after"}
        assert outputs[0]["output_sample"][0]["order_id"] == "A-1"
        assert outputs[0]["curated_dataset_id"] == stats["curated_dataset_ids"][0]
    finally:
        run_db.close()
