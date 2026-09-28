"""量纲推导引擎：词法分析 → 递归下降解析 → 量纲/数值求值。

支持：
  - 四则运算、幂运算（^ 或 **，右结合）、括号、一元正负号
  - 隐式乘法：``2 m``、``2(1+1)``、``kg m/s^2``、``2π r``
  - 数字后跟单位：``9.8 m/s^2``、``1.5e-3 mol``
  - 上标 Unicode：``m²``、``x⁻¹``
  - 全角/符号运算符：× ÷ · −
  - 函数调用：sqrt / abs / sin / cos / tan / asin / acos / atan / exp / ln / log / log2
  - 变量引用（变量值本身也是带单位的表达式，可相互引用，检测循环）

所有错误都携带 (start, end) 片段位置，指回原始表达式字符串。
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from fractions import Fraction

from .units import (
    CONSTANTS, DIMLESS, DIM_NAMES, FUNCTIONS, UNITS,
    derived_matches, dim_str, format_fraction, si_unit_str,
)

# ---------------------------------------------------------------------------
# 错误
# ---------------------------------------------------------------------------

@dataclass
class AnalysisError:
    message: str
    start: int
    end: int
    kind: str = "error"
    source: str = "expression"  # expression / target / var:<name>

    def to_dict(self) -> dict:
        return {
            "message": self.message,
            "start": self.start,
            "end": self.end,
            "kind": self.kind,
            "source": self.source,
        }


class EngineError(Exception):
    """携带片段位置的引擎内部异常。"""

    def __init__(self, message: str, start: int, end: int, kind: str = "error"):
        super().__init__(message)
        self.error = AnalysisError(message, start, end, kind)


# ---------------------------------------------------------------------------
# 词法分析
# ---------------------------------------------------------------------------

@dataclass
class Token:
    kind: str          # num / ident / op / eof
    text: str
    start: int
    end: int
    value: float = 0.0


_NUM_RE = re.compile(r"(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?")

_SUPERSCRIPTS = {
    "⁰": "0", "¹": "1", "²": "2", "³": "3", "⁴": "4",
    "⁵": "5", "⁶": "6", "⁷": "7", "⁸": "8", "⁹": "9", "⁻": "-",
}

# 单字符运算符（含常见全角/符号别名，长度均为 1，不影响片段定位）
_CHAR_OPS = {
    "+": "+", "-": "-", "−": "-",
    "*": "*", "×": "*", "·": "*", "⋅": "*",
    "/": "/", "÷": "/",
    "^": "^", "(": "(", ")": ")", ",": ",",
}

_IDENT_EXTRA_START = "_°%"   # isalpha() 之外的标识符起始字符
_IDENT_EXTRA_CONT = "_"


def tokenize(src: str) -> list[Token]:
    tokens: list[Token] = []
    i, n = 0, len(src)
    while i < n:
        ch = src[i]
        if ch.isspace():
            i += 1
            continue
        # 注意：'²' 等上标字符的 isdigit() 为真，数字判断必须限定 ASCII
        if ch in "0123456789" or (ch == "." and i + 1 < n and src[i + 1] in "0123456789"):
            m = _NUM_RE.match(src, i)
            assert m is not None
            text = m.group()
            tokens.append(Token("num", text, i, m.end(), float(text)))
            i = m.end()
            continue
        if ch in _SUPERSCRIPTS:
            j = i
            digits = ""
            while j < n and src[j] in _SUPERSCRIPTS:
                digits += _SUPERSCRIPTS[src[j]]
                j += 1
            # 上标序列 → 幂运算：^ <digits>（^ 为零宽 token，指向上标起点）
            tokens.append(Token("op", "^", i, i))
            tokens.append(Token("num", digits, i, j, float(digits)))
            i = j
            continue
        if ch.isalpha() or ch in _IDENT_EXTRA_START:
            j = i + 1
            # 注意：'²' 等上标字符的 isalnum() 为真，必须显式排除
            while j < n and src[j] not in _SUPERSCRIPTS and (
                src[j].isalnum() or src[j] in _IDENT_EXTRA_CONT
            ):
                j += 1
            tokens.append(Token("ident", src[i:j], i, j))
            i = j
            continue
        if ch == "*" and i + 1 < n and src[i + 1] == "*":
            tokens.append(Token("op", "^", i, i + 2))
            i += 2
            continue
        if ch in _CHAR_OPS:
            tokens.append(Token("op", _CHAR_OPS[ch], i, i + 1))
            i += 1
            continue
        raise EngineError(f"无法识别的字符「{ch}」", i, i + 1, "syntax")
    tokens.append(Token("eof", "", n, n))
    return tokens


# ---------------------------------------------------------------------------
# 语法分析（递归下降）
# ---------------------------------------------------------------------------

@dataclass
class Node:
    kind: str                       # num / name / neg / bin / pow / call / group
    start: int
    end: int
    value: float = 0.0
    name: str = ""
    op: str = ""
    left: "Node | None" = None
    right: "Node | None" = None
    args: list = field(default_factory=list)


class Parser:
    """文法（优先级由低到高）：
        add  := mul  (('+'|'-') mul)*
        mul  := unary (('*'|'/'|隐式) unary)*
        unary:= ('-'|'+') unary | pow
        pow  := primary ('^' unary)?          （右结合；指数允许负号）
        primary := NUMBER | IDENT | IDENT '(' args ')' | '(' add ')'
    隐式乘法：primary 之后紧跟 NUMBER / IDENT / '(' 时视为相乘。
    """

    def __init__(self, tokens: list[Token]):
        self.toks = tokens
        self.i = 0

    def peek(self) -> Token:
        return self.toks[self.i]

    def advance(self) -> Token:
        tok = self.toks[self.i]
        self.i += 1
        return tok

    def expect_op(self, op: str) -> Token:
        tok = self.peek()
        if tok.kind != "op" or tok.text != op:
            raise EngineError(f"此处应为「{op}」", tok.start, max(tok.end, tok.start + 1), "syntax")
        return self.advance()

    def parse(self) -> Node:
        if self.peek().kind == "eof":
            raise EngineError("表达式为空", 0, 0, "syntax")
        node = self.parse_add()
        tok = self.peek()
        if tok.kind != "eof":
            raise EngineError(f"此处有多余的内容「{tok.text}」", tok.start, max(tok.end, tok.start + 1), "syntax")
        return node

    def parse_add(self) -> Node:
        left = self.parse_mul()
        while self.peek().kind == "op" and self.peek().text in ("+", "-"):
            op = self.advance()
            right = self.parse_mul()
            left = Node("bin", left.start, right.end, op=op.text, left=left, right=right)
        return left

    def parse_mul(self) -> Node:
        left = self.parse_unary()
        while True:
            tok = self.peek()
            if tok.kind == "op" and tok.text in ("*", "/"):
                self.advance()
                right = self.parse_unary()
                left = Node("bin", left.start, right.end, op=tok.text, left=left, right=right)
            elif tok.kind in ("num", "ident") or (tok.kind == "op" and tok.text == "("):
                right = self.parse_unary()  # 隐式乘法
                left = Node("bin", left.start, right.end, op="*", left=left, right=right)
            else:
                return left

    def parse_unary(self) -> Node:
        tok = self.peek()
        if tok.kind == "op" and tok.text == "-":
            self.advance()
            operand = self.parse_unary()
            return Node("neg", tok.start, operand.end, left=operand)
        if tok.kind == "op" and tok.text == "+":
            self.advance()
            return self.parse_unary()
        return self.parse_pow()

    def parse_pow(self) -> Node:
        base = self.parse_primary()
        tok = self.peek()
        if tok.kind == "op" and tok.text == "^":
            self.advance()
            exponent = self.parse_unary()
            return Node("pow", base.start, exponent.end, left=base, right=exponent)
        return base

    def parse_primary(self) -> Node:
        tok = self.peek()
        if tok.kind == "num":
            self.advance()
            return Node("num", tok.start, tok.end, value=tok.value)
        if tok.kind == "ident":
            self.advance()
            name = tok.text
            nxt = self.peek()
            if name in FUNCTIONS and nxt.kind == "op" and nxt.text == "(":
                self.advance()
                args: list[Node] = []
                if not (self.peek().kind == "op" and self.peek().text == ")"):
                    args.append(self.parse_add())
                    while self.peek().kind == "op" and self.peek().text == ",":
                        self.advance()
                        args.append(self.parse_add())
                rpar = self.expect_op(")")
                return Node("call", tok.start, rpar.end, name=name, args=args)
            return Node("name", tok.start, tok.end, name=name)
        if tok.kind == "op" and tok.text == "(":
            self.advance()
            inner = self.parse_add()
            rpar = self.expect_op(")")
            return Node("group", tok.start, rpar.end, left=inner)
        raise EngineError("此处应为数字、变量、单位或括号", tok.start, max(tok.end, tok.start + 1), "syntax")


# ---------------------------------------------------------------------------
# 求值（数值 + 量纲递归推导）
# ---------------------------------------------------------------------------

@dataclass
class Quantity:
    value: float      # 以 SI 基本单位计的数值
    dim: tuple        # 量纲向量


def _dim_add(a: tuple, b: tuple) -> tuple:
    return tuple(x + y for x, y in zip(a, b))


def _dim_sub(a: tuple, b: tuple) -> tuple:
    return tuple(x - y for x, y in zip(a, b))


def _dim_scale(d: tuple, k: Fraction) -> tuple:
    return tuple(x * k for x in d)


class Evaluator:
    """对 AST 递归求值。resolve(name, node) 由外层注入，用于解析变量/单位/常量。"""

    def __init__(self, resolve):
        self.resolve = resolve

    def eval(self, node: Node) -> Quantity:
        kind = node.kind
        if kind == "num":
            return Quantity(node.value, DIMLESS)
        if kind == "group":
            return self.eval(node.left)
        if kind == "name":
            return self.resolve(node.name, node)
        if kind == "neg":
            q = self.eval(node.left)
            return Quantity(-q.value, q.dim)
        if kind == "call":
            args = [self.eval(a) for a in node.args]
            return self._call(node, args)
        if kind == "bin":
            return self._bin(node)
        if kind == "pow":
            return self._pow(node)
        raise EngineError(f"内部错误：未知节点 {kind}", node.start, node.end)

    # -- 二元运算 -----------------------------------------------------------
    def _bin(self, node: Node) -> Quantity:
        left = self.eval(node.left)
        right = self.eval(node.right)
        op = node.op
        if op in ("+", "-"):
            if left.dim != right.dim:
                word = "加" if op == "+" else "减"
                raise EngineError(
                    f"量纲不一致：「{dim_str(left.dim)}」与「{dim_str(right.dim)}」不能相{word}",
                    node.right.start, node.right.end, "dim_mismatch",
                )
            value = left.value + right.value if op == "+" else left.value - right.value
            return Quantity(value, left.dim)
        if op == "*":
            return Quantity(left.value * right.value, _dim_add(left.dim, right.dim))
        if op == "/":
            if right.value == 0:
                raise EngineError("除数为零", node.right.start, node.right.end, "value")
            return Quantity(left.value / right.value, _dim_sub(left.dim, right.dim))
        raise EngineError(f"内部错误：未知运算符 {op}", node.start, node.end)

    # -- 幂运算 -------------------------------------------------------------
    def _pow(self, node: Node) -> Quantity:
        base = self.eval(node.left)
        expo = self.eval(node.right)
        if expo.dim != DIMLESS:
            raise EngineError(
                f"幂指数必须无量纲（实际量纲为 {dim_str(expo.dim)}）",
                node.right.start, node.right.end, "bad_exponent",
            )
        k = Fraction(expo.value).limit_denominator(1_000_000)
        try:
            value = base.value ** float(k)
        except ZeroDivisionError:
            raise EngineError("零的负数次幂无意义", node.start, node.end, "value") from None
        except OverflowError:
            raise EngineError("数值溢出", node.start, node.end, "value") from None
        if isinstance(value, complex):
            raise EngineError("负数不能取分数次幂", node.start, node.end, "value")
        return Quantity(value, _dim_scale(base.dim, k))

    # -- 函数调用 -----------------------------------------------------------
    def _call(self, node: Node, args: list[Quantity]) -> Quantity:
        name = node.name
        meta = FUNCTIONS[name]
        if len(args) != meta["arity"]:
            raise EngineError(
                f"函数 {name} 需要 {meta['arity']} 个参数，实际给了 {len(args)} 个",
                node.start, node.end, "arity",
            )
        arg_node = node.args[0]
        q = args[0]
        if name == "sqrt":
            if q.value < 0:
                raise EngineError("sqrt 的参数不能为负数", arg_node.start, arg_node.end, "value")
            return Quantity(math.sqrt(q.value), _dim_scale(q.dim, Fraction(1, 2)))
        if name == "abs":
            return Quantity(abs(q.value), q.dim)
        if q.dim != DIMLESS:
            raise EngineError(
                f"函数 {name} 的参数必须无量纲（实际量纲为 {dim_str(q.dim)}）",
                arg_node.start, arg_node.end, "dimless_arg",
            )
        fn = {
            "sin": math.sin, "cos": math.cos, "tan": math.tan,
            "asin": math.asin, "acos": math.acos, "atan": math.atan,
            "exp": math.exp, "ln": math.log, "log": math.log10, "log2": math.log2,
        }[name]
        try:
            return Quantity(fn(q.value), DIMLESS)
        except ValueError:
            raise EngineError(f"函数 {name} 的参数超出定义域", arg_node.start, arg_node.end, "value") from None
        except OverflowError:
            raise EngineError("数值溢出", arg_node.start, arg_node.end, "value") from None


# ---------------------------------------------------------------------------
# 顶层分析入口
# ---------------------------------------------------------------------------

# 双侧差分的相对步长因子（central difference，取截断误差 O(h²) 与
# float64 舍入误差 O(eps/h) 的平衡点；零标称值时 h=因子·u 仍为有效小步长）
UNCERTAINTY_STEP = 1e-5


def _parse(src: str) -> Node:
    return Parser(tokenize(src)).parse()


def _eval_src(src: str, resolve) -> Quantity:
    """词法 + 语法 + 求值一条龙，resolve 由外层模型注入。"""
    return Evaluator(resolve).eval(_parse(src))


def _collect_names(node: Node, out: set[str]) -> None:
    """收集 AST 中出现的全部标识符（变量名/单位名/常量名/函数名）。"""
    if node.kind == "name":
        out.add(node.name)
    if node.left is not None:
        _collect_names(node.left, out)
    if node.right is not None:
        _collect_names(node.right, out)
    for arg in node.args:
        _collect_names(arg, out)


class _Model:
    """已解析的变量链 + 主表达式；可按给定“扰动覆盖值”反复重算。"""

    def __init__(self, var_ast: dict[str, Node]):
        self.var_ast = var_ast

    def context(self, overrides: dict[str, Quantity] | None = None,
                error_sink: list | None = None):
        """返回 (resolve, cache, failed)。

        cache 预置扰动覆盖值，整条变量链随取随算；变量自身的根因错误会
        记入 error_sink（source 标记为 var:<名称>），依赖失败经包装后向上抛。
        """
        cache: dict[str, Quantity] = dict(overrides or {})
        failed: dict[str, EngineError] = {}
        stack: list[str] = []

        def evaluate_variable(name: str) -> Quantity:
            stack.append(name)
            try:
                q = Evaluator(resolve).eval(self.var_ast[name])
            except EngineError as exc:
                failed[name] = exc
                # source 仍是 "expression" 表示错误出自本变量自身的定义式；
                # 依赖传导上来的包装错误已带 var:<上游名>，保留不动，避免重复登记
                if exc.error.source == "expression":
                    exc.error.source = f"var:{name}"
                    if error_sink is not None:
                        error_sink.append(exc.error)
                raise
            except OverflowError:
                err = EngineError("数值溢出", 0, 0, "value")
                err.error.source = f"var:{name}"
                failed[name] = err
                if error_sink is not None:
                    error_sink.append(err.error)
                raise err
            finally:
                stack.pop()
            cache[name] = q
            return q

        def resolve(name: str, node: Node) -> Quantity:
            if name in cache:
                return cache[name]
            if name in failed:
                raise EngineError(
                    f"变量「{name}」计算出错：{failed[name].error.message}",
                    node.start, node.end, "var_error",
                )
            if name in self.var_ast:
                if name in stack:
                    chain = " → ".join(stack + [name])
                    raise EngineError(f"变量循环引用：{chain}", node.start, node.end, "var_cycle")
                return evaluate_variable(name)
            if name in CONSTANTS:
                value, d, _ = CONSTANTS[name]
                return Quantity(value, d)
            if name in UNITS:
                d, factor, _, _ = UNITS[name]
                return Quantity(factor, d)
            raise EngineError(f"未定义的变量或单位「{name}」", node.start, node.end, "undefined")

        return resolve, cache, failed

    def full_eval(self, expression: str, main_ast: Node,
                  variables: dict[str, str], overrides: dict[str, Quantity]):
        """用扰动覆盖值重算完整变量链与主表达式；任一结果非有限即报错。"""
        resolve, cache, failed = self.context(overrides)
        # 经 resolve 驱动全部变量（依赖项随取随算并写入 cache），任一失败即终止
        for name in self.var_ast:
            q = resolve(name, Node("name", 0, 0, name=name))
            if not math.isfinite(q.value):
                raise EngineError(
                    f"变量「{name}」扰动后的取值不是有限数值",
                    0, len(variables[name]), "value",
                )
        result = Evaluator(resolve).eval(main_ast)
        if not math.isfinite(result.value):
            raise EngineError("扰动后的主表达式结果不是有限数值", 0, len(expression), "value")
        return cache, result


def analyze(expression: str, variables: dict | None = None,
            target_unit: str | None = None,
            uncertainties: dict | None = None) -> dict:
    """分析表达式，返回可 JSON 序列化的结果字典。

    即使存在错误也会尽量返回已算出的部分；errors 中每项都带有
    source（expression / target / var:<名称> / unc:<名称>）与 (start, end) 片段位置。

    不确定度分析：以每个填写了正标准不确定度的直接输入变量为独立扰动源，
    对完整变量链与主表达式做双侧小步长重算，按一阶中心差分与平方和（RSS）
    给出结果的合成标准不确定度。标称计算或任一扰动出错时只报错、不给数值。
    """
    expression = expression or ""
    variables = {
        str(k).strip(): (v if isinstance(v, str) else str(v))
        for k, v in (variables or {}).items() if str(k).strip()
    }
    uncertainties = {
        str(k).strip(): (v if isinstance(v, str) else str(v))
        for k, v in (uncertainties or {}).items() if str(k).strip()
    }
    errors: list[AnalysisError] = []
    # 变量名与内置单位/常量同名时给出非致命警告（变量优先级更高）
    warnings = []
    for name in variables:
        if name in UNITS:
            warnings.append({"message": f"变量「{name}」与内置单位同名，所有表达式中的 {name} 将按变量解析",
                             "name": name, "kind": "shadow_unit"})
        elif name in CONSTANTS:
            warnings.append({"message": f"变量「{name}」与内置常量同名，所有表达式中的 {name} 将按变量解析",
                             "name": name, "kind": "shadow_constant"})

    # --- 1) 语法分析：变量定义式 / 不确定度式 / 主表达式 -------------------
    var_ast: dict[str, Node] = {}
    for name, src in variables.items():
        try:
            var_ast[name] = _parse(src)
        except EngineError as exc:
            exc.error.source = f"var:{name}"
            errors.append(exc.error)

    unc_ast: dict[str, Node] = {}
    for name, src in uncertainties.items():
        if name not in variables:
            errors.append(AnalysisError(
                f"为变量「{name}」填写了标准不确定度，但变量列表中没有该变量",
                0, len(src), "uncertainty_unknown", f"unc:{name}",
            ))
            continue
        try:
            unc_ast[name] = _parse(src)
        except EngineError as exc:
            exc.error.source = f"unc:{name}"
            errors.append(exc.error)

    # 给“非直接输入”（定义式引用了其他变量）却又指定不确定度的情况发警告
    for name in unc_ast:
        if name not in var_ast:
            continue
        referenced: set[str] = set()
        _collect_names(var_ast[name], referenced)
        if referenced & set(variables):
            warnings.append({
                "message": f"变量「{name}」的取值表达式引用了其他变量，却又单独指定了标准不确定度；"
                           f"它会被当作独立扰动源，可能与其上游变量的不确定度被重复计入",
                "name": name, "kind": "uncertainty_independent",
            })

    main_ast: Node | None = None
    try:
        main_ast = _parse(expression)
    except EngineError as exc:
        exc.error.source = "expression"
        errors.append(exc.error)

    model = _Model(var_ast)

    # --- 2) 标称求值：全部变量（含未被引用者）+ 主表达式 ------------------
    # 变量自身的根因错误与依赖链上的包装错误均由 context 的 error_sink 记录；
    # 经 resolve 驱动可保证每个变量的成功取值/失败标记都填入 cache/failed
    resolve0, cache0, failed0 = model.context(error_sink=errors)
    for name in var_ast:
        try:
            resolve0(name, Node("name", 0, 0, name=name))
        except (EngineError, OverflowError):
            pass

    result: Quantity | None = None
    if main_ast is not None:
        try:
            result = Evaluator(resolve0).eval(main_ast)
            if not math.isfinite(result.value):
                raise EngineError("计算结果不是有限数值", 0, len(expression), "value")
        except EngineError as exc:
            # 来自变量定义/依赖链的错误已带具体 source，只登记不覆盖
            if exc.error.source == "expression":
                errors.append(exc.error)
        except OverflowError:
            err = EngineError("数值溢出", 0, len(expression), "value")
            err.error.source = "expression"
            errors.append(err.error)

    # --- 3) 不确定度校验 + 双侧小步长扰动 ---------------------------------
    uncertainty_block: dict | None = None
    # 3a) 不确定度本身的校验（非负 / 有限 / 量纲一致）独立进行，
    #     即使主表达式或其他变量出错，也能指出该不确定度输入的问题
    unc_qty: dict[str, Quantity] = {}
    if unc_ast and cache0.keys() >= set(unc_ast):
        for name, ast in unc_ast.items():
            src = uncertainties[name]
            try:
                q = Evaluator(resolve0).eval(ast)
            except EngineError as exc:
                exc.error.source = f"unc:{name}"
                errors.append(exc.error)
                continue
            if not math.isfinite(q.value):
                err = EngineError("标准不确定度必须是有限数值", 0, len(src), "uncertainty_value")
                err.error.source = f"unc:{name}"
                errors.append(err.error)
                continue
            if q.value < 0:
                err = EngineError(
                    "标准不确定度必须为非负值（它表示标准差，不能为负）",
                    0, len(src), "uncertainty_value",
                )
                err.error.source = f"unc:{name}"
                errors.append(err.error)
                continue
            nominal = cache0[name]
            if q.dim != nominal.dim:
                err = EngineError(
                    f"变量「{name}」的量纲为「{dim_str(nominal.dim)}」，但标准不确定度量纲为"
                    f"「{dim_str(q.dim)}」，两者必须一致",
                    0, len(src), "uncertainty_dim",
                )
                err.error.source = f"unc:{name}"
                errors.append(err.error)
                continue
            unc_qty[name] = q

    # 3b) 只有标称模型（变量链 + 主表达式）完全无错、且不确定度也全部合法时，
    #     才进行扰动汇总，避免输出貌似有效的数值
    if result is not None and not errors and unc_qty:
        sum_sq = 0.0
        var_sum_sq = {name: 0.0 for name in var_ast}
        sources_info: list[dict] = []
        aborted = False
        for name, uq in unc_qty.items():
            u = uq.value
            if u == 0:
                # 零不确定度不构成扰动源，不列入 sources，对平方和贡献也为零
                continue
            x = cache0[name].value
            # 零标称值时以不确定度本身作为尺度，保证步长仍然有效（h>0）
            h = UNCERTAINTY_STEP * max(abs(x), u)

            def root_message(exc: Exception) -> str:
                """沿 __cause__ 链回溯到最深层的引擎错误（变量链根因）。"""
                deepest = exc
                while getattr(deepest, "__cause__", None) is not None:
                    deepest = deepest.__cause__
                return getattr(getattr(deepest, "error", None), "message", "数值溢出")

            def attempt(side: str, offset: float):
                try:
                    return model.full_eval(
                        expression, main_ast, variables,
                        {name: Quantity(x + offset, uq.dim)},
                    ), None
                except (EngineError, OverflowError) as exc:
                    msg = root_message(exc)
                    return None, (
                        f"扰动源变量「{name}」向{side}方向偏移 "
                        f"{h:.6g} {si_unit_str(uq.dim) or '1'} 时计算失败：{msg}；"
                        f"已停止不确定度汇总，未给出合成结果"
                    )

            res_pos, err_pos = attempt("正（+）", h)
            res_neg, err_neg = (None, None)
            if err_pos is None:
                res_neg, err_neg = attempt("负（−）", -h)
            if err_pos or err_neg:
                msg = err_pos or err_neg
                span_src = uncertainties.get(name) or variables.get(name, "")
                errors.append(AnalysisError(
                    msg, 0, len(span_src), "uncertainty_eval", f"unc:{name}",
                ))
                aborted = True
                break
            cache_p, yp = res_pos
            cache_m, ym = res_neg

            ci = abs(yp.value - ym.value) / (2.0 * h) * u
            sum_sq += ci * ci
            for j in var_ast:
                cij = abs(cache_p[j].value - cache_m[j].value) / (2.0 * h) * u
                var_sum_sq[j] += cij * cij
            sources_info.append({
                "name": name,
                "nominal_si": x,
                "uncertainty_si": u,
                "step_si": h,
                "contribution_si": ci,
            })

        if not aborted:
            u_result = math.sqrt(sum_sq)
            uncertainty_block = {
                "si_value": u_result,
                "si_unit": si_unit_str(result.dim),
                "relative": (u_result / abs(result.value)) if result.value != 0 else None,
                "step_factor": UNCERTAINTY_STEP,
                "sources": sources_info,
                "variables": [
                    {
                        "name": name,
                        "si_value": cache0[name].value,
                        "uncertainty_si": math.sqrt(var_sum_sq[name]),
                        "dimension_str": dim_str(cache0[name].dim),
                        "si_unit": si_unit_str(cache0[name].dim),
                    }
                    for name in var_ast
                ],
            }

    # --- 4) 目标单位换算（标称值与不确定度按同一因子一起换算）-------------
    target = None
    if target_unit and target_unit.strip():
        try:
            tq = _eval_src(target_unit.strip(), resolve0)
            if tq.value == 0:
                raise EngineError("目标单位不能为零", 0, len(target_unit), "value")
            if result is not None:
                if tq.dim != result.dim:
                    raise EngineError(
                        f"目标单位量纲为「{dim_str(tq.dim)}」，与结果量纲「{dim_str(result.dim)}」不一致",
                        0, len(target_unit), "target_dim",
                    )
                target = {"unit": target_unit.strip(), "value": result.value / tq.value}
                if uncertainty_block is not None:
                    target["uncertainty"] = uncertainty_block["si_value"] / tq.value
        except EngineError as exc:
            exc.error.source = "target"
            errors.append(exc.error)

    response: dict = {
        "ok": not errors,
        "errors": [e.to_dict() for e in errors],
        "warnings": warnings,
        "result": None,
    }
    if result is not None:
        response["result"] = {
            "si_value": result.value,
            "dimension": [format_fraction(x) for x in result.dim],
            "dimension_names": DIM_NAMES,
            "dimension_str": dim_str(result.dim),
            "si_unit": si_unit_str(result.dim),
            "derived": derived_matches(result.dim),
            "target": target,
        }
        if uncertainty_block is not None:
            response["result"]["uncertainty"] = uncertainty_block
    return response
