"""GGB 脚本确定性预检（零 LLM 成本）。

阶段0 实测：脚本类命令(SetColor/SetValue…)在 evalCommand 里**成功也返回 false**，
前端无法据布尔判错——所以「未知命令 / 未定义引用 / 中文标点 / 括号不配平」
这类必挂问题必须在这里静态拦住，带精确行号直接喂修复模型。

机械修正：中文标点(引号/逗号/括号/分号/冒号) → ASCII（仅字符串外；中文引号
本身按字符串边界成对转换），不耗自愈回合。
"""
from __future__ import annotations

import re

# ── 命令白名单 ──────────────────────────────────────────────
# 创建类：产生新对象（前端可用 labels/exists 判败）
CREATION_COMMANDS = {
    "Point", "Midpoint", "Segment", "Line", "Ray", "Vector", "Polygon",
    "RegularPolygon", "Circle", "Semicircle", "Arc", "CircularArc",
    "CircularSector", "Ellipse", "Hyperbola", "Parabola", "Conic",
    "Angle", "Slider", "Checkbox", "Button", "InputBox", "Text",
    "FormulaText", "LaTeX", "Intersect", "Tangent", "PerpendicularLine",
    "OrthogonalLine", "PerpendicularBisector", "LineBisector", "AngleBisector",
    "AngularBisector", "Distance", "Length", "Area", "Perimeter", "Radius",
    "Slope", "Function", "If", "Sequence", "Zip", "Min", "Max", "Sum",
    "Mean", "Median", "Mode", "SD", "Rotate", "Reflect", "Mirror",
    "Translate", "Dilate", "Locus", "Derivative", "Integral", "IntegralBetween",
    "Root", "Roots", "Extremum", "InflectionPoint", "Asymptote", "Vertex",
    "Focus", "Directrix", "Center", "Centroid", "Incircle", "Circumcircle",
    "TriangleCenter", "Sphere", "Cube", "Pyramid", "Prism", "Cone", "Cylinder",
    "Plane", "Tetrahedron", "Net", "BarChart", "Histogram", "PieChart",
    "BoxPlot", "DotPlot", "RandomBetween", "RandomUniform", "Polyline",
    "UnitVector", "CrossRatio", "Curve", "Spline", "Fit", "FitLine", "FitPoly",
    "Corner", "Name", "DynamicCoordinates", "ClosestPoint", "PointIn",
}
# 脚本类：改属性无新对象（evalCommand 布尔不可用 → 引用必须静态校验）
SCRIPTING_COMMANDS = {
    "SetValue", "SetColor", "SetCaption", "ShowLabel", "SetVisibleInView",
    "SetConditionToShowObject", "SetLineThickness", "SetLineStyle",
    "SetPointSize", "SetPointStyle", "SetFilling", "SetDynamicColor",
    "SetFixed", "SetTrace", "StartAnimation", "SetLabelMode", "SetLayer",
    "ZoomIn", "ZoomOut", "Pan", "Delete", "SetActiveView", "SetAxesVisible",
    "SetGridVisible", "SetBackgroundColor", "CenterView", "SetSeed",
    "SetPerspective", "SetSpinSpeed", "SetViewDirection", "PlaySound",
    "SetTooltipMode", "TurnOffAnimation? ",
}
SCRIPTING_COMMANDS = {c.strip("? ") for c in SCRIPTING_COMMANDS}
ALL_COMMANDS = CREATION_COMMANDS | SCRIPTING_COMMANDS

# 数学函数/常量（小写调用不按命令校验）
MATH_FUNCS = {
    "sin", "cos", "tan", "asin", "acos", "atan", "atan2", "sinh", "cosh",
    "tanh", "sqrt", "cbrt", "abs", "exp", "ln", "log", "log2", "log10",
    "floor", "ceil", "round", "sgn", "random", "min", "max", "mod",
}
BUILTIN_NAMES = {"x", "y", "z", "t", "k", "n", "pi", "e", "true", "false",
                 "xAxis", "yAxis", "zAxis", "i", "j"}

# 脚本类命令中「第一个参数必须是已定义对象」的清单（Zoom/Pan/视图类除外）
_REF_FIRST_ARG = {
    "SetValue", "SetColor", "SetCaption", "ShowLabel", "SetVisibleInView",
    "SetConditionToShowObject", "SetLineThickness", "SetLineStyle",
    "SetPointSize", "SetPointStyle", "SetFilling", "SetDynamicColor",
    "SetFixed", "SetTrace", "StartAnimation", "SetLabelMode", "SetLayer",
    "Delete",
}

_CJK_PUNCT = {"，": ",", "。": ".", "（": "(", "）": ")", "；": ";",
              "：": ":", "、": ",", "＝": "=", "《": "\"", "》": "\""}

# ZoomIn 命令实测会让 LaTeX 文本消失（阶段0 复测），视窗一律走 `# view:` 指令
# （前端用 JS API setCoordSystem 实现）。4 数值形式机械转换，其余报 issue。
_ZOOMIN_RE = re.compile(
    r"^\s*ZoomIn\(\s*(-?[\d.]+)\s*,\s*(-?[\d.]+)\s*,\s*(-?[\d.]+)\s*,\s*(-?[\d.]+)\s*\)\s*$")
_LABEL_RE = re.compile(r"^([A-Za-z一-鿿][A-Za-z0-9_一-鿿]*)\s*(?:\([A-Za-z ,]*\))?\s*=")
_CMD_CALL_RE = re.compile(r"([A-Za-z][A-Za-z0-9]*)\s*\(")
_IDENT = r"[A-Za-z一-鿿][A-Za-z0-9_一-鿿']*"


def _fix_punct(line: str) -> str:
    """机械修正：中文引号按出现次序当成对引号转 ASCII；其余中文标点在字符串外转 ASCII。"""
    line = line.replace("“", '"').replace("”", '"').replace("‘", '"').replace("’", '"')
    out, in_str = [], False
    for ch in line:
        if ch == '"':
            in_str = not in_str
            out.append(ch)
        elif not in_str and ch in _CJK_PUNCT:
            out.append(_CJK_PUNCT[ch])
        else:
            out.append(ch)
    return "".join(out)


def _strip_strings(line: str) -> str:
    """把字符串字面量抠掉（保留占位），便于只检查命令结构。"""
    return re.sub(r'"[^"]*"', '""', line)


def _first_arg(line: str, cmd: str) -> str:
    m = re.search(re.escape(cmd) + r"\s*\(\s*(" + _IDENT + r")\s*[,)]", line)
    return m.group(1) if m else ""


def preflight(script: str) -> tuple[str, list[dict]]:
    """返回 (机械修正后的脚本, 必挂问题列表 [{line, msg}])。

    问题列表非空时执行必然出错/静默失效——调用方应把这些精确问题直接喂修复模型。
    """
    lines = script.splitlines()
    fixed: list[str] = []
    issues: list[dict] = []
    defined: set[str] = set()
    latex_texts: set[str] = set()

    for n, raw in enumerate(lines, 1):
        line = _fix_punct(raw.rstrip())
        zm = _ZOOMIN_RE.match(line)
        if zm:      # 机械转换：ZoomIn(xmin,ymin,xmax,ymax) → # view: 指令
            line = f"# view: {zm.group(1)} {zm.group(2)} {zm.group(3)} {zm.group(4)}"
        fixed.append(line)
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if re.search(r"\bZoomIn\s*\(", s) or re.search(r"\bZoomOut\s*\(", s):
            issues.append({"line": n, "msg": f"第 {n} 行使用了 ZoomIn/ZoomOut（该命令会导致 LaTeX "
                                             f"公式消失）——设定视窗请改用指令行 "
                                             f"`# view: xmin ymin xmax ymax`"})
            continue
        bare = _strip_strings(s)

        # 括号/引号配平
        if s.count('"') % 2 != 0:
            issues.append({"line": n, "msg": f"第 {n} 行引号不配对：{s[:60]}"})
            continue
        if bare.count("(") != bare.count(")"):
            issues.append({"line": n, "msg": f"第 {n} 行括号不配平：{s[:60]}"})
            continue
        # 残留 CJK 字符出现在字符串外（中文只能进引号）
        m_cjk = re.search(r"[一-鿿＀-￯]", bare)
        if m_cjk and not _LABEL_RE.match(bare):
            issues.append({"line": n, "msg": f"第 {n} 行中文出现在字符串外（命令/参数必须英文，"
                                             f"中文只能写在双引号内）：{s[:60]}"})
            continue

        # 命令调用校验（首字母大写的调用视为命令）
        bad = False
        for cmd in _CMD_CALL_RE.findall(bare):
            if cmd[0].isupper() and cmd not in ALL_COMMANDS:
                if cmd == "SetCoordSystem":
                    issues.append({"line": n, "msg": f"第 {n} 行 `SetCoordSystem(...)` 不是 GGB 命令——"
                                                     f"设定视窗请改用 ZoomIn(xmin,ymin,xmax,ymax)"})
                else:
                    issues.append({"line": n, "msg": f"第 {n} 行 `{cmd}(...)` 不在本环境命令清单里"
                                                     f"（可能拼错或不存在）——换成速查表里确定存在的命令"})
                bad = True
        if bad:
            continue

        # 脚本类命令：第一参必须已定义
        m = _CMD_CALL_RE.search(bare)
        if m and m.group(1) in _REF_FIRST_ARG:
            ref = _first_arg(bare, m.group(1))
            if ref and ref not in defined and ref not in BUILTIN_NAMES:
                issues.append({"line": n, "msg": f"第 {n} 行 {m.group(1)}({ref},...) 引用了"
                                                 f"未定义的对象 `{ref}`（该命令会静默失效）——"
                                                 f"先定义它或改成已有对象"})
                continue

        # 记录本行定义的标签（顺带记下 LaTeX 文本对象）
        lm = _LABEL_RE.match(bare)
        if lm:
            name = lm.group(1)
            if name in defined:
                issues.append({"line": n, "msg": f"第 {n} 行重复定义 `{name}`（会覆盖旧对象、"
                                                 f"依赖它的对象可能失效）——换一个新标签"})
            defined.add(name)
            if re.search(r"=\s*Text\(.*true\s*,\s*true\s*\)", s):
                latex_texts.add(name)

    # 机械修正：对 LaTeX 文本的 SetColor（视窗重绘后公式会消失——实测引擎 bug）直接移除
    for i, line in enumerate(fixed):
        m = re.match(r"\s*SetColor\(\s*(" + _IDENT + r")\s*,", line)
        if m and m.group(1) in latex_texts:
            fixed[i] = f"# （已自动移除：SetColor({m.group(1)},…) 会让 LaTeX 公式消失）"

    return "\n".join(fixed), issues


def known_labels(script: str) -> list[str]:
    """脚本里定义过的标签（喂修复模型的「已知对象表」补充）。"""
    out = []
    for raw in script.splitlines():
        m = _LABEL_RE.match(_strip_strings(raw.strip()))
        if m:
            out.append(m.group(1))
    return out
