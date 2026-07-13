"""确定性 3D 脚本整理。

模型负责数学构造，本模块只处理能机械判断的视觉规则：补齐明确拓扑中的棱、
统一点/线/面的标签与尺寸、限制面填充，并清除重复点名。所有自动追加内容都放在
标记块中，重复调用时会先删除旧块再重建，便于自愈与定向编辑反复经过流水线。
"""
from __future__ import annotations

import re


_AUTO_BLOCK_RE = re.compile(
    r"\n?# a2g:3d-normalize begin\n.*?# a2g:3d-normalize end\n?",
    re.DOTALL,
)
_ASSIGN_RE = re.compile(
    r"^\s*([A-Za-z][A-Za-z0-9_]*)\s*=\s*([A-Za-z][A-Za-z0-9_]*)?\s*(.*)$"
)
_POINT_RE = re.compile(
    r"^\s*([A-Za-z][A-Za-z0-9_]*)\s*=\s*\(\s*[^,()]+\s*,\s*[^,()]+\s*,\s*[^,()]+\s*\)\s*$"
)
_POLYGON_RE = re.compile(
    r"^\s*([A-Za-z][A-Za-z0-9_]*)\s*=\s*Polygon\s*\(\s*([^()]*)\s*\)\s*$",
    re.IGNORECASE,
)
_SEGMENT_RE = re.compile(
    r"^\s*([A-Za-z][A-Za-z0-9_]*)\s*=\s*Segment\s*\(\s*"
    r"([A-Za-z][A-Za-z0-9_]*)\s*,\s*([A-Za-z][A-Za-z0-9_]*)\s*\)\s*$",
    re.IGNORECASE,
)
_OBJECT_ASSIGN_RE = re.compile(
    r"^\s*([A-Za-z][A-Za-z0-9_]*)\s*=\s*"
    r"(Segment|Polygon|Plane|Cube|Prism|Pyramid|Tetrahedron|Polyhedron|"
    r"Sphere|Cone|Cylinder)\s*\(",
    re.IGNORECASE,
)
_STYLE_RE = re.compile(
    r"^\s*(ShowLabel|SetPointSize|SetLineThickness|SetFilling)\s*\(\s*"
    r"([A-Za-z][A-Za-z0-9_]*)\s*,",
    re.IGNORECASE,
)
_FILL_RE = re.compile(
    r"^(\s*SetFilling\s*\(\s*([A-Za-z][A-Za-z0-9_]*)\s*,\s*)"
    r"(-?\d+(?:\.\d+)?)(\s*\)\s*)$",
    re.IGNORECASE,
)
_DUPLICATE_POINT_TEXT_RE = re.compile(
    r'^\s*[A-Za-z][A-Za-z0-9_]*\s*=\s*Text\s*\(\s*"([A-Za-z][A-Za-z0-9_]*)"\s*,\s*'
    r"([A-Za-z][A-Za-z0-9_]*)\s*(?:,\s*true\s*)?\)\s*$",
    re.IGNORECASE,
)

_SOLID_RE = re.compile(
    r"\b(?:Cube|Prism|Pyramid|Tetrahedron|Polyhedron)\s*\(", re.IGNORECASE
)
_PYRAMID_RE = re.compile(
    r"^\s*[A-Za-z][A-Za-z0-9_]*\s*=\s*Pyramid\s*\(\s*"
    r"([A-Za-z][A-Za-z0-9_]*)\s*,\s*([A-Za-z][A-Za-z0-9_]*)\s*\)",
    re.IGNORECASE,
)


def _clean_auto_block(script: str) -> str:
    return _AUTO_BLOCK_RE.sub("\n", script).strip()


def _point_list(source: str) -> list[str]:
    values = [part.strip() for part in source.split(",")]
    return values if len(values) >= 3 and all(re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", v) for v in values) else []


def _edge_key(a: str, b: str) -> tuple[str, str]:
    return tuple(sorted((a, b)))


def _safe_edge_label(a: str, b: str, used: set[str]) -> str:
    stem = f"a2gEdge_{a}_{b}"
    label = stem
    suffix = 2
    while label in used:
        label = f"{stem}_{suffix}"
        suffix += 1
    used.add(label)
    return label


def _topology_edges(script: str, points: set[str], polygons: dict[str, list[str]]) -> list[tuple[str, str]]:
    """只为拓扑明确的常见立体补棱，避免猜测任意点集。"""
    if not _SOLID_RE.search(script):
        return []

    edges: list[tuple[str, str]] = []

    # 两个等长多边形且顶点一一对应（A↔A1 或 A↔E）时，补上下底与侧棱。
    polygon_values = list(polygons.values())
    for i, first in enumerate(polygon_values):
        for second in polygon_values[i + 1:]:
            if len(first) != len(second) or len(first) < 3:
                continue
            suffix_pairs = all(b == f"{a}1" for a, b in zip(first, second))
            if not suffix_pairs:
                continue
            for ring in (first, second):
                edges.extend((ring[n], ring[(n + 1) % len(ring)]) for n in range(len(ring)))
            edges.extend(zip(first, second))

    # 常见 Cube(A,B) 生成后模型往往继续引用 A..H；这些点全部显式存在时拓扑唯一。
    cube_letters = set("ABCDEFGH")
    if re.search(r"\bCube\s*\(", script, re.IGNORECASE) and cube_letters.issubset(points):
        edges.extend((a, b) for a, b in (
            ("A", "B"), ("B", "C"), ("C", "D"), ("D", "A"),
            ("E", "F"), ("F", "G"), ("G", "H"), ("H", "E"),
            ("A", "E"), ("B", "F"), ("C", "G"), ("D", "H"),
        ))

    # Pyramid(basePolygon, apex) 可以从命令本身无歧义地得到底面与侧棱。
    for line in script.splitlines():
        match = _PYRAMID_RE.match(line)
        if not match:
            continue
        base, apex = match.groups()
        ring = polygons.get(base, [])
        if ring and apex in points:
            edges.extend((ring[n], ring[(n + 1) % len(ring)]) for n in range(len(ring)))
            edges.extend((vertex, apex) for vertex in ring)

    result: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for a, b in edges:
        key = _edge_key(a, b)
        if a in points and b in points and key not in seen:
            result.append((a, b))
            seen.add(key)
    return result


def normalize_script(script: str) -> str:
    """返回视觉规则整理后的 3D 脚本；不改变数学对象的坐标与依赖。"""
    cleaned = _clean_auto_block(script)
    raw_lines = cleaned.splitlines()

    points: set[str] = set()
    polygons: dict[str, list[str]] = {}
    segments: dict[str, tuple[str, str]] = {}
    object_types: dict[str, str] = {}
    styles: set[tuple[str, str]] = set()
    used_labels: set[str] = set()

    for line in raw_lines:
        point_match = _POINT_RE.match(line)
        if point_match:
            points.add(point_match.group(1))
        polygon_match = _POLYGON_RE.match(line)
        if polygon_match:
            polygons[polygon_match.group(1)] = _point_list(polygon_match.group(2))
        segment_match = _SEGMENT_RE.match(line)
        if segment_match:
            segments[segment_match.group(1)] = (segment_match.group(2), segment_match.group(3))
        object_match = _OBJECT_ASSIGN_RE.match(line)
        if object_match:
            object_types[object_match.group(1)] = object_match.group(2).lower()
        style_match = _STYLE_RE.match(line)
        if style_match:
            styles.add((style_match.group(1).lower(), style_match.group(2)))
        assign_match = _ASSIGN_RE.match(line)
        if assign_match:
            used_labels.add(assign_match.group(1))

    # 删除 Text("A", A) 这类与点自带标签完全重复的文字；保留真正的说明文字。
    lines: list[str] = []
    for line in raw_lines:
        duplicate = _DUPLICATE_POINT_TEXT_RE.match(line)
        if duplicate and duplicate.group(1) == duplicate.group(2) and duplicate.group(2) in points:
            continue
        fill = _FILL_RE.match(line)
        if fill and fill.group(2) in polygons:
            value = min(max(float(fill.group(3)), 0.0), 0.12)
            rendered = f"{value:.2f}".rstrip("0").rstrip(".")
            line = f"{fill.group(1)}{rendered}{fill.group(4)}"
        lines.append(line)

    existing_edges = {_edge_key(a, b) for a, b in segments.values()}
    auto: list[str] = []
    for a, b in _topology_edges(cleaned, points, polygons):
        if _edge_key(a, b) in existing_edges:
            continue
        label = _safe_edge_label(a, b, used_labels)
        auto.extend((
            f"{label}=Segment({a},{b})",
            f"SetLineThickness({label},3)",
            f"ShowLabel({label},false)",
        ))
        existing_edges.add(_edge_key(a, b))

    for point in sorted(points):
        if ("setpointsize", point) not in styles:
            auto.append(f"SetPointSize({point},5)")
        if ("showlabel", point) not in styles:
            auto.append(f"ShowLabel({point},true)")

    for label, object_type in object_types.items():
        if object_type == "segment":
            if ("setlinethickness", label) not in styles:
                auto.append(f"SetLineThickness({label},3)")
            if ("showlabel", label) not in styles:
                auto.append(f"ShowLabel({label},false)")
        elif object_type == "polygon":
            if ("setfilling", label) not in styles:
                auto.append(f"SetFilling({label},0.08)")
            if ("showlabel", label) not in styles:
                auto.append(f"ShowLabel({label},false)")
        elif object_type in {"plane", "cube", "prism", "pyramid", "tetrahedron", "polyhedron"}:
            if ("showlabel", label) not in styles:
                auto.append(f"ShowLabel({label},false)")

    if not auto and lines == raw_lines:
        return cleaned.rstrip() + "\n"

    block = ["# a2g:3d-normalize begin", *auto, "# a2g:3d-normalize end"]
    return "\n".join([*lines, *block]).rstrip() + "\n"
