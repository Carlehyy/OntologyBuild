"""safe_eval 回归测试。

object_set 函数（及 Panel 里的 expression 默认模板）依赖列表/生成器推导式；
曾因 _ALLOWED_NODES 缺 ast.Store（推导式循环变量是 Name(ctx=Store)）而全部报
"不允许的语法节点: Store"。这里锁死推导式可用，同时确认赋值类构造仍被拒。
"""
import pytest

from app.ontologies.formal_modeling.safe_eval import safe_eval, SafeEvalError

OBJS = [
    {"available_qty": 5, "min_qty": 10},
    {"available_qty": 120, "min_qty": 30},
    {"available_qty": 8, "min_qty": 20},
    {"available_qty": 60, "min_qty": 15},
]


def test_list_comprehension_projection():
    assert safe_eval("[o.available_qty for o in objects]", {"objects": OBJS}) == [5, 120, 8, 60]


def test_utils_sum_over_comprehension():
    assert safe_eval("utils.sum([o.available_qty for o in objects])", {"objects": OBJS}) == 193


def test_filter_comprehension_count():
    expr = "utils.count([o for o in objects if o.available_qty < o.min_qty])"
    assert safe_eval(expr, {"objects": OBJS}) == 2


def test_default_object_set_template_runs():
    """Panel 里 expression 版 object_set 的默认模板必须能跑。"""
    objs = [{"status": "active"}, {"status": "idle"}]
    assert safe_eval('[o for o in objects if o.status == "active"]', {"objects": objs}) == [{"status": "active"}]


def test_generator_expression_in_sum():
    assert safe_eval("sum(o.available_qty for o in objects)", {"objects": OBJS}) == 193


def test_walrus_assignment_still_blocked():
    """Store 只该服务于推导式目标；带副作用的赋值表达式（海象）仍须被拒。"""
    with pytest.raises(SafeEvalError):
        safe_eval("(x := 5)", {})
