"""哨兵运行时的向后兼容入口。

真实实现已迁移到 ``app.ontologies.sentinels``。这里必须保持惰性加载：
正规 Mapping 在冷启动的 Celery worker 中会先导入 canonical CDC，而 CDC
又需要 evaluator；若包初始化时反向 eager import CDC，会读取到尚未完成
初始化的模块并导致 ``register_cdc`` ImportError。
"""
from importlib import import_module


_EXPORTS = {
    "register_cdc": ("app.ontologies.sentinels.cdc", "register_cdc"),
    "stop_cdc_worker": (
        "app.ontologies.sentinels.cdc", "stop_cdc_worker"),
    "start_scan_worker": (
        "app.ontologies.sentinels.scan_worker", "start_scan_worker"),
    "stop_scan_worker": (
        "app.ontologies.sentinels.scan_worker", "stop_scan_worker"),
    "run_manual": ("app.ontologies.sentinels.engine", "run_manual"),
    "run_for_change": ("app.ontologies.sentinels.engine", "run_for_change"),
    "run_scheduled": ("app.ontologies.sentinels.engine", "run_scheduled"),
}

__all__ = list(_EXPORTS)


def __getattr__(name: str):
    target = _EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attribute_name = target
    value = getattr(import_module(module_name), attribute_name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
