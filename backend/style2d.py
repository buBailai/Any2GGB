"""确定性 2D 线条样式整理。

GeoGebra 的白色画布上，模型依赖默认颜色或输出浅色 SetColor 都可能让
曲线看起来透明。本模块只处理能机械判断的平面线对象：缺省时补深色与
可读线宽，并把白/浅色线收口为深灰。多边形的边是 GeoGebra 额外生成的
Segment 对象，由前端执行层把多边形样式同步到这些边。
"""
from __future__ import annotations

import re


_AUTO_BLOCK_RE = re.compile(
    r"\n?# a2g:2d-style begin\n.*?# a2g:2d-style end\n?",
    re.DOTALL,
)
_ASSIGN_RE = re.compile(
    r"^\s*([A-Za-z][A-Za-z0-9_]*)\s*=\s*([A-Za-z][A-Za-z0-9_]*)\s*\(",
    re.IGNORECASE,
)
_COLOR_RE = re.compile(
    r"^(\s*SetColor\s*\(\s*([A-Za-z][A-Za-z0-9_]*)\s*,\s*)"
    r"(\d+(?:\.\d+)?)\s*,\s*(\d+(?:\.\d+)?)\s*,\s*(\d+(?:\.\d+)?)"
    r"(\s*\)\s*)$",
    re.IGNORECASE,
)
_STYLE_RE = re.compile(
    r"^\s*(SetColor|SetLineThickness)\s*\(\s*"
    r"([A-Za-z][A-Za-z0-9_]*)\s*,",
    re.IGNORECASE,
)

_LINE_TYPES = {
    "segment", "line", "ray", "vector", "polyline",
    "circle", "semicircle", "arc", "circulararc", "circularsector",
    "ellipse", "hyperbola", "parabola", "conic", "tangent",
    "perpendicularline", "orthogonalline", "perpendicularbisector",
    "linebisector", "anglebisector", "angularbisector",
}
_POLYGON_TYPES = {"polygon", "regularpolygon"}


def _is_too_light(red: float, green: float, blue: float) -> bool:
    """按感知亮度判断白底低对比色；饱和的红/蓝等强调色仍保留。"""
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue >= 180


def normalize_script(script: str) -> str:
    """补齐2D线条的默认深色样式，并消除明显不可见的浅色线。"""
    cleaned = _AUTO_BLOCK_RE.sub("\n", script).strip()
    raw_lines = cleaned.splitlines()
    object_types: dict[str, str] = {}
    styles: set[tuple[str, str]] = set()

    for line in raw_lines:
        assigned = _ASSIGN_RE.match(line)
        if assigned:
            object_types[assigned.group(1)] = assigned.group(2).lower()
        styled = _STYLE_RE.match(line)
        if styled:
            styles.add((styled.group(1).lower(), styled.group(2)))

    line_labels = {
        label for label, kind in object_types.items()
        if kind in _LINE_TYPES or kind in _POLYGON_TYPES
    }
    lines: list[str] = []
    for line in raw_lines:
        color = _COLOR_RE.match(line)
        if color and color.group(2) in line_labels:
            rgb = tuple(float(color.group(i)) for i in (3, 4, 5))
            if _is_too_light(*rgb):
                line = f"{color.group(1)}35,35,35{color.group(6)}"
        lines.append(line)

    auto: list[str] = []
    for label, kind in object_types.items():
        # Polygon 的可见边不是 polygon 对象本身，由执行层处理。
        if kind not in _LINE_TYPES:
            continue
        if ("setcolor", label) not in styles:
            auto.append(f"SetColor({label},35,35,35)")
        if ("setlinethickness", label) not in styles:
            auto.append(f"SetLineThickness({label},3)")

    if not auto and lines == raw_lines:
        return cleaned.rstrip() + "\n"
    if auto:
        lines.extend(("# a2g:2d-style begin", *auto, "# a2g:2d-style end"))
    return "\n".join(lines).rstrip() + "\n"
