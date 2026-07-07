"""
安全表达式求值器 (Safe Expression Evaluator)

后端用纯 Python AST 白名单求值，绝不 eval/exec 任意代码。
支持本体函数的 'expression' 语言：算术、比较、逻辑、属性访问、下标、
三元、列表/字典字面量，以及一组白名单工具函数 (sum/avg/min/max/len/count...)。

可用变量（由调用方注入 scope）：
  object   当前对象实例 properties（object 函数）
  objects  对象集合 properties 列表（object_set 函数）
  params   函数参数
  utils    工具函数命名空间
"""
from __future__ import annotations
import ast
from typing import Any

MAX_NODES = 2000


class SafeEvalError(Exception):
    pass


class _AttrDict(dict):
    """既支持 obj['key'] 又支持 obj.key 的字典；缺失键返回 None（对齐前端 JS 的 undefined 语义）。

    用于把注入作用域的 object / params / objects[i] 等 dict 透明包装，
    使本体函数表达式可以写成 Palantir 风格的 object.score / params.threshold，
    也兼容 object['score']。
    """

    def __getattr__(self, name: str) -> Any:  # noqa: D401
        if name.startswith("__"):
            raise AttributeError(name)
        return self.get(name)

    def __getitem__(self, key: Any) -> Any:
        return dict.get(self, key)


def wrap_scope_value(value: Any) -> Any:
    """递归把 dict→_AttrDict、list/tuple 元素同样包装，便于属性访问。"""
    if isinstance(value, _AttrDict):
        return value
    if isinstance(value, dict):
        return _AttrDict({k: wrap_scope_value(v) for k, v in value.items()})
    if isinstance(value, (list, tuple)):
        return [wrap_scope_value(v) for v in value]
    return value


# 允许的 AST 节点
_ALLOWED_NODES = (
    ast.Expression, ast.Constant, ast.Name, ast.Load,
    ast.BinOp, ast.UnaryOp, ast.BoolOp, ast.Compare, ast.IfExp,
    ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow,
    ast.USub, ast.UAdd, ast.Not,
    ast.And, ast.Or,
    ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.In, ast.NotIn,
    ast.List, ast.Tuple, ast.Dict, ast.Set,
    ast.Subscript, ast.Index if hasattr(ast, "Index") else ast.Slice, ast.Slice,
    ast.Attribute, ast.Call, ast.keyword,
    ast.ListComp, ast.comprehension, ast.GeneratorExp,
    ast.Starred,
)


def _sum(arr):
    return sum((_num(x) for x in arr), 0)


def _num(x):
    try:
        return float(x) if x is not None and not isinstance(x, bool) else 0
    except (TypeError, ValueError):
        return 0


_UTILS = {
    "sum": _sum,
    "avg": lambda arr: (_sum(arr) / len(arr)) if arr else 0,
    "count": lambda arr: len(arr),
    "len": lambda arr: len(arr) if arr is not None else 0,
    "min": lambda arr: min((_num(x) for x in arr), default=0),
    "max": lambda arr: max((_num(x) for x in arr), default=0),
    "round": lambda x, n=0: round(_num(x), int(n)),
    "abs": lambda x: abs(_num(x)),
    "lower": lambda s: str(s).lower() if s is not None else "",
    "upper": lambda s: str(s).upper() if s is not None else "",
    "contains": lambda s, sub: (sub in s) if s is not None else False,
    "now": lambda: __import__("datetime").datetime.now().isoformat(),
}

# 允许在表达式里直接调用的全局名（属性访问 utils.xxx 也可）
_SAFE_NAMES = dict(_UTILS)
_SAFE_NAMES.update({
    "True": True, "False": False, "None": None,
    "true": True, "false": False, "null": None,
})


def _check(node: ast.AST):
    count = 0
    for n in ast.walk(node):
        count += 1
        if count > MAX_NODES:
            raise SafeEvalError("表达式过于复杂")
        if not isinstance(n, _ALLOWED_NODES):
            raise SafeEvalError(f"不允许的语法节点: {type(n).__name__}")
        # 禁止 dunder 属性访问
        if isinstance(n, ast.Attribute) and n.attr.startswith("__"):
            raise SafeEvalError("禁止访问 dunder 属性")
        if isinstance(n, ast.Name) and n.id.startswith("__"):
            raise SafeEvalError("禁止访问 dunder 名称")


def safe_eval(expr: str, scope: dict[str, Any]) -> Any:
    """对单个表达式求值。expr 可以是多行但必须是单个表达式。"""
    expr = (expr or "").strip()
    if not expr:
        return None
    # 兼容 JS 风格：去掉外层括号、末尾分号
    expr = expr.rstrip(";").strip()
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as e:
        raise SafeEvalError(f"表达式语法错误: {e}")
    _check(tree)

    full_scope = dict(_SAFE_NAMES)
    # 把注入的作用域变量（object/params/objects...）包装成支持属性访问的 _AttrDict
    for k, v in (scope or {}).items():
        full_scope[k] = wrap_scope_value(v)
    # utils 命名空间（支持 utils.sum 形式）
    full_scope.setdefault("utils", _Namespace(_UTILS))

    try:
        code = compile(tree, "<expr>", "eval")
        return eval(code, {"__builtins__": {}}, full_scope)  # noqa: S307 — AST 已白名单校验
    except SafeEvalError:
        raise
    except Exception as e:
        raise SafeEvalError(str(e))


class _Namespace:
    """把 dict 包装成可以 .attr 访问的对象（用于 utils.sum）"""
    def __init__(self, d: dict):
        for k, v in d.items():
            setattr(self, k, v)
