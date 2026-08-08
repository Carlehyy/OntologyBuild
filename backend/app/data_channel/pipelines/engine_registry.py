"""
采集引擎注册表 — 接入新流水线来源（第三方工作流平台等）的唯一挂点。

一条流水线的 definition.engine 决定行数据从哪来：
  - 缺省/"canvas"  画布引擎：数据集 → route A/B/C steps（pipeline_run_task 内置）
  - "n8n"          数据管家治理的 n8n 工作流（steward 模块）
  - 未来引擎        在 _BUILTIN 登记，或运行时 register_engine()

所有引擎的产物殊途同归：list[dict] 行数据 → 资产湖准入闸门（lake_gate）→
curated 版本化入湖。新引擎只需要实现「怎么取到行数据」（一个 collector），
入湖校验/合并/记账复用 external_runner，不必各写一套。

接入新引擎的最小步骤：
  1. 写一个 collector(db, pipeline) -> (rows, exec_meta)（参考 steward/runner.py
     的 n8n collector：触发远端 → 等执行 → 规整为 list[dict]）
  2. 用 external_runner.run_external_pipeline 包一个 runner(db, pl, run, write_opts)
  3. 在下方 _BUILTIN 登记 "引擎名" -> ("模块路径", "runner 函数名")
调度、任务池、入湖方式、主键契约全部自动生效。
"""
from __future__ import annotations

import importlib
import logging
from typing import Callable

logger = logging.getLogger(__name__)

# 运行时注册的引擎（测试/插件用）
_RUNNERS: dict[str, Callable] = {}

# 内置引擎：惰性 import，避免 steward ↔ pipeline_run 的环形依赖
_BUILTIN: dict[str, tuple[str, str]] = {
    "n8n": ("app.data_channel.steward.runner", "run_n8n_pipeline"),
    "python": (
        "app.data_channel.pipelines.python_engine.runner",
        "run_python_pipeline",
    ),
}

# 画布引擎的别名：definition.engine 为这些值时走 pipeline_run_task 内置路径
CANVAS_ENGINES = (None, "", "canvas")


def register_engine(name: str, runner: Callable) -> None:
    """运行时登记引擎。runner 签名：fn(db, pipeline, run, write_opts) -> None，
    负责把 run 写到终态（success/failed）。"""
    _RUNNERS[name] = runner


def get_engine_runner(name: str) -> Callable | None:
    """按引擎名取 runner；未知引擎返回 None（调用方应让 run 明确失败）。"""
    if name in _RUNNERS:
        return _RUNNERS[name]
    spec = _BUILTIN.get(name)
    if spec is None:
        return None
    module_path, attr = spec
    try:
        runner = getattr(importlib.import_module(module_path), attr)
    except Exception:  # noqa: BLE001 — 引擎模块坏了不该炸掉整个任务进程
        logger.error("加载引擎 %s (%s.%s) 失败", name, module_path, attr, exc_info=True)
        return None
    _RUNNERS[name] = runner
    return runner


def known_engines() -> list[str]:
    return sorted({*_RUNNERS.keys(), *_BUILTIN.keys()})
