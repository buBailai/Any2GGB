"""生成场景与空间维度定义。

前端只拿公开文案；真正影响模型行为的专业约束留在后端，避免把一大段
提示词塞进请求，也让不同入口得到一致结果。
"""
from __future__ import annotations


MODES = {
    "free": {
        "key": "free",
        "title": "自由",
        "description": "按描述自由绘制，不额外假设用途",
        "placeholder": "描述你想画的数学或物理图形……",
        "prompt": (
            "【自由模式】忠实理解用户的显式需求；信息不足时采用最简、最稳妥的"
            "数学表达，不擅自增加解题步骤、结论或装饰。"
        ),
    },
    "solve": {
        "key": "solve",
        "title": "解题",
        "description": "围绕解题关系安排主体与辅助线",
        "placeholder": "粘贴题目或描述已知条件、待求量，AI 会规划解题配图……",
        "prompt": (
            "【解题模式】先识别已知量、待求量和关键几何/物理关系，再安排主体图形、"
            "必要辅助线、等量/垂直/平行/受力标记。图中只呈现对解题有帮助的信息；"
            "不要在图里写大段解答，不虚构题目未给的数据。"
        ),
    },
    "figure": {
        "key": "figure",
        "title": "配图",
        "description": "生成清晰、规范、可直接用于题目的配图",
        "placeholder": "描述题目配图，如：直角三角形两直角边 3、4，标注三边和直角符号……",
        "prompt": (
            "【配图模式】产出可直接放进试卷、讲义或课件的规范插图。优先保证比例、"
            "标注、黑白可印和留白；只保留题目需要的元素。"
        ),
    },
    "replicate": {
        "key": "replicate",
        "title": "复刻",
        "description": "依参考图还原结构、标注与构图",
        "placeholder": "上传或粘贴参考图，并说明需要保留、修正或去除的细节……",
        "prompt": (
            "【复刻模式】把参考图当作主要视觉证据：逐项核对点、线、曲线、角标、"
            "文字、相对位置和比例，先还原再清理。不得用相似但不同的图代替；图中"
            "模糊或矛盾处才依据用户文字与数学关系做最小修正。"
        ),
    },
}

SPACES = {
    "2d": {"key": "2d", "title": "2D", "description": "平面几何、函数与二维物理示意"},
    "3d": {"key": "3d", "title": "3D", "description": "立体几何与三维物理模型"},
}


def normalize_mode(value: str) -> str:
    return value if value in MODES else "figure"


def normalize_space(value: str) -> str:
    return value if value in SPACES else "2d"


def prompt_for(mode: str) -> str:
    return MODES[normalize_mode(mode)]["prompt"]


def public_list() -> list[dict]:
    return [
        {k: v for k, v in item.items() if k != "prompt"}
        for item in MODES.values()
    ]


def public_spaces() -> list[dict]:
    return list(SPACES.values())
