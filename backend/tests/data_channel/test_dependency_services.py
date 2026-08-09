from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.data_channel.datasets.consumers import (
    dataset_consumer_map,
    dataset_consumers,
)
from app.data_channel.pipelines.dependency_service import (
    reject_if_sync_chain_refs,
)


class _Query:
    def __init__(self, rows):
        self._rows = rows

    def filter(self, *_criteria):
        return self

    def all(self):
        return self._rows


class _Db:
    def __init__(self, rows):
        self._rows = rows

    def query(self, _model):
        return _Query(self._rows)


def test_dataset_consumer_map_uses_source_dataset_reference_only():
    """canvas 下线后只经 source_dataset_id 绑定源数据集；画布节点引用不再扫描。"""
    first = SimpleNamespace(
        id="pipeline-1",
        name="订单入湖",
        status="published",
        domain="供应链",
        source_dataset_id="dataset-a",
        definition={"engine": "python", "python": {"script": "result = []"}},
    )
    second = SimpleNamespace(
        id="pipeline-2",
        name="补充数据",
        status=None,
        domain=None,
        source_dataset_id=None,
        definition={
            "nodes": [{
                "type": "connector",
                "config": {"files": [{"dataset_id": "dataset-b"}]},
            }],
        },
    )
    db = _Db([first, second])

    mapping = dataset_consumer_map(db)

    assert mapping["dataset-a"] == [{
        "id": "pipeline-1",
        "name": "订单入湖",
        "status": "published",
        "domain": "供应链",
    }]
    # 存量画布 connector.files 引用不再计入消费方
    assert dataset_consumers(db, "dataset-b") == []
    assert dataset_consumers(db, "unreferenced") == []


def test_sync_chain_dependency_blocks_pipeline_lifecycle_change():
    references = [
        SimpleNamespace(name="每日订单同步"),
        SimpleNamespace(name="供应商同步"),
        SimpleNamespace(name="库存同步"),
        SimpleNamespace(name="价格同步"),
    ]

    with pytest.raises(HTTPException) as exc_info:
        reject_if_sync_chain_refs(
            _Db(references),
            "pipeline-1",
            action="归档",
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == (
        "流水线被 4 个同步任务设为链式触发目标"
        "（每日订单同步、供应商同步、库存同步…），不能归档。"
        "请先在这些同步任务中解除「同步后触发流水线」的配置。"
    )


def test_pipeline_without_sync_chain_reference_is_not_blocked():
    assert (
        reject_if_sync_chain_refs(
            _Db([]),
            "pipeline-1",
            action="删除",
        )
        is None
    )
