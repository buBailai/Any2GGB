"""核心引擎：绘图方案规划 → GGB 脚本两段式 + 修复/定向编辑提示词。

与 Any2Manim 的差异：执行与验证发生在前端（浏览器 GGB 引擎），
本模块只产脚本与修复；自愈回合的流程控制在 main.py（等前端回报结果）。
"""
from __future__ import annotations

import json
import re
from typing import Optional

from . import config, modes

# ── 提示词片段 ──────────────────────────────────────────────


def _frag(name: str) -> str:
    p = config.PROMPTS_DIR / name
    return p.read_text(encoding="utf-8") if p.exists() else ""


_PRINCIPLES = _frag("figure_principles.md")


def _cheatsheet() -> str:
    return _frag("ggb_command_cheatsheet.md")


def _interaction_rules(interactive: bool) -> str:
    if interactive:
        return (
            "【互动模式】界面已明确勾选互动。必须根据本题核心变量设计真正有教学意义的互动："
            "说明控件类型、控制对象、取值范围/初值与操作后可观察的变化。优先选择滑杆、"
            "受约束动点或勾选框；不得放一个与图形无关的装饰控件。"
        )
    return (
        "【静态模式】界面未勾选互动，必须输出纯画图。即使用户文字提到滑杆、动点、动画或"
        "互动，也不要设计互动控件；interaction 必须为空，不使用 Slider、Point(对象)、"
        "Checkbox、Button、InputBox、StartAnimation 或条件显隐。"
    )


def sys_plan(mode: str = "figure", space: str = "2d", interactive: bool = False) -> str:
    mode = modes.normalize_mode(mode)
    space = modes.normalize_space(space)
    space_rules = (
        "【输出空间：3D】方案必须使用三维坐标与立体对象，明确关键顶点、棱、面、截面/投影"
        "或三维力矢量；不要用二维透视假画冒充 3D。"
        if space == "3d" else
        "【输出空间：2D】方案只使用平面对象与二维坐标；不要打开 3D 视图或生成三维对象。"
    )
    return (
    "你是数学/物理老师的【题目配图】规划师。把老师的一句话需求，规划成一份"
    "清晰的绘图方案。严格遵守以下配图原则：\n\n"
    f"{_PRINCIPLES}\n\n"
    f"{modes.prompt_for(mode)}\n{space_rules}\n{_interaction_rules(interactive)}\n\n"
    "只输出一个 JSON 对象（不要 markdown 围栏、不要解释），结构：\n"
    "{\n"
    '  "brief": {"topic": "图形主题", "audience": "学段(可留空)", "core_claim": "这张图要呈现的核心内容(一句)"},\n'
    '  "steps": [\n'
    '    {"id": "step_01", "teaches": "这一步画什么(一句)", "shows": "画面新增哪些对象/标注"}\n'
    "  ],\n"
    '  "interaction": "互动模式填写具体控件、范围、初值、控制对象和可观察变化；静态模式必须填空字符串 \\"\\""\n'
    "}\n"
    "规则：2~5 个 step，按「主体图形 → 标注 → 补充说明」的顺序；"
    "互动设置只由界面开关决定，优先级高于用户文字。"
    )


SYS_PLAN = sys_plan()


# 亲测可跑的 few-shot 范例（静态配图为主 + 按需交互；线条默认黑色）
_FEWSHOT = """# step_01：视窗与直角三角形
# perspective: 2d
# view: -1.5 -1.5 6.5 5
A=(0,0)
B=(4,0)
C=(0,3)
tri=Polygon(A,B,C)
outline=Polyline(A,B,C,A)
SetColor(outline,0,0,0)
SetLineThickness(outline,4)
SetFilling(tri,0.08)
# step_02：直角符号与边长标注
ang=Angle(B,A,C)
lb=Text("b=3",(-0.8,1.4))
la=Text("a=4",(1.8,-0.8))
lc=Text("c=5",(2.3,1.9))
# step_03：公式说明（LaTeX，保持默认黑色）
t1=Text("a^2+b^2=c^2",(3.6,3.8),true,true)"""

_FEWSHOT_INTERACT = """（仅当方案 interaction 非空时，按此风格追加交互，例）
k=Slider(-3,3,0.1)
f(x)=k*x^2
StartAnimation(k)"""


def sys_scriptgen(mode: str = "figure", space: str = "2d",
                  interactive: bool = False) -> str:
    mode = modes.normalize_mode(mode)
    space = modes.normalize_space(space)
    if space == "3d":
        space_rules = (
            "【输出空间：3D】脚本第一条有效指令必须是 `# perspective: 3d`；可按需再写 "
            "`# view3d: xmin ymin zmin xmax ymax zmax`。所有自由点写三维坐标 `(x,y,z)`；"
            "主体必须由真实 3D 对象构成。不要写 2D 的 `# view:`。"
        )
        example = """# perspective: 3d
# view3d: -1 -1 -1 5 5 5
# step_01：建立正方体
A=(0,0,0)
B=(3,0,0)
cu=Cube(A,B)
# step_02：标出体对角线
diag=Segment(A,G)
SetLineThickness(diag,5)"""
    else:
        space_rules = (
            "【输出空间：2D】脚本第一条有效指令必须是 `# perspective: 2d`，并在开头用 "
            "`# view: xmin ymin xmax ymax` 设置二维视窗；严禁三维坐标与 3D 对象。"
        )
        example = _FEWSHOT
    interaction_script_rule = (
        "interaction 中设计的控件必须真的创建，并与核心图形建立动态依赖。\n"
        if interactive else "这是纯静态图，严禁创建或启动任何互动控件。\n"
    )
    interaction_example = (
        f"互动控件风格参考：\n{_FEWSHOT_INTERACT}\n\n" if interactive else ""
    )
    return (
        "你是 GeoGebra 配图工程师。把下面的【绘图方案】逐 step 翻译成可直接执行的 "
        "GeoGebra Classic 命令脚本——忠实翻译方案，不要自由发挥。\n"
        f"遵守以下配图原则：\n\n{_PRINCIPLES}\n\n"
        f"{modes.prompt_for(mode)}\n{space_rules}\n{_interaction_rules(interactive)}\n\n"
        f"再严格遵守《命令速查/避坑清单》——与之冲突即错误：\n\n{_cheatsheet()}\n\n"
        "⚠️ 完整翻译：方案里每个 step 都要落地，段首写注释 `# step_01：<这一步画什么>`；"
        f"{interaction_script_rule}"
        f"参考范例（照此风格）：\n{example}\n\n{interaction_example}"
        "只输出命令脚本文本：一行一条命令，无 markdown 围栏、无解释。"
    )


def sys_fix(mode: str = "figure", space: str = "2d", interactive: bool = False) -> str:
    space = modes.normalize_space(space)
    return (
        "你是 GeoGebra 脚本调试专家。下面的脚本有几行执行失败/存在必挂问题。"
        "请修复并返回【完整脚本】（不是 diff、不要 markdown 围栏、不要解释）。\n"
        "⚠️ 只修问题行，保留全部绘图步骤与 `# step_NN：...` 注释；"
        "严禁为了跑通就删减图形要素或标注。\n"
        f"必须保留 `# perspective: {space}`，且不得把 {space.upper()} 脚本改成另一维度。\n"
        f"{_interaction_rules(interactive)}\n"
        f"环境约束（修复时同样必须遵守，违反即错误）：\n\n{_cheatsheet()}"
    )


def sys_edit(mode: str = "figure", space: str = "2d", interactive: bool = False) -> str:
    space = modes.normalize_space(space)
    return (
    "你是 GeoGebra 脚本定向编辑器。给你当前完整脚本与老师的修改要求，"
    "只输出【最小改动的 search/replace 块】，可多个，格式严格：\n"
    "<<<<<<< SEARCH\n<精确粘贴要替换的旧片段>\n=======\n<新片段>\n>>>>>>> REPLACE\n"
    "SEARCH 必须与原脚本逐字一致。不要输出完整脚本、不要解释、不要 markdown 围栏。"
    f"只改与要求相关的部分；务必保留 `# step_NN：...` 注释行和 `# perspective: {space}`；"
    f"保持 {space.upper()} 空间，不得切换维度。{_interaction_rules(interactive)}"
    )


SYS_EDIT = sys_edit()


def script_space(script: str) -> str:
    m = re.search(r"^\s*#\s*perspective\s*:\s*(2d|3d)\s*$", script, re.IGNORECASE | re.MULTILINE)
    if m:
        return m.group(1).lower()
    return "3d" if re.search(r"\([^,()]+,[^,()]+,[^,()]+\)|\b(?:Sphere|Cube|Prism|Pyramid|Plane)\s*\(", script) else "2d"


def ensure_space_directive(script: str, space: str) -> str:
    """把空间选择变成执行侧可依赖的确定性指令，而不是只相信模型会照写。"""
    space = modes.normalize_space(space)
    directive = f"# perspective: {space}"
    pat = re.compile(r"^\s*#\s*perspective\s*:\s*(?:2d|3d)\s*$", re.IGNORECASE | re.MULTILINE)
    if pat.search(script):
        return pat.sub(directive, script, count=1)
    return directive + "\n" + script.lstrip()


_INTERACTION_CONTROL_RE = re.compile(
    r"\b(?:Slider|Point|Checkbox|Button|InputBox|DynamicCoordinates)\s*\(",
    re.IGNORECASE)
_INTERACTION_RE = re.compile(
    r"\b(?:Slider|Point|Checkbox|Button|InputBox|StartAnimation|"
    r"SetConditionToShowObject|DynamicCoordinates)\s*\(", re.IGNORECASE)


def script_is_interactive(script: str) -> bool:
    """判断脚本是否含系统认可的互动控件/行为。"""
    return bool(_INTERACTION_CONTROL_RE.search(script))


def interaction_issues(script: str, interactive: bool) -> list[dict]:
    """把互动开关变成自愈回路可执行的确定性约束。"""
    matches: list[tuple[int, str]] = []
    for line_no, raw in enumerate(script.splitlines(), 1):
        if _INTERACTION_RE.search(raw):
            matches.append((line_no, raw.strip()))
    if interactive and not _INTERACTION_CONTROL_RE.search(script):
        return [{"line": 0, "msg": "已勾选互动，但脚本没有创建任何有效互动控件。请根据需求加入"
                                     "滑杆、受约束动点或勾选框，并让它实际控制核心图形。"}]
    if not interactive and matches:
        detail = "；".join(f"第 {n} 行 {line[:50]}" for n, line in matches[:5])
        return [{"line": matches[0][0], "msg": "未勾选互动，必须是纯静态图；请移除所有互动控件/"
                                                  f"动画及其依赖，改成固定对象。发现：{detail}"}]
    return []


# ── 工具函数 ────────────────────────────────────────────────
def extract_script(raw: str) -> str:
    """去掉可能的 markdown 围栏/前后废话，保留纯脚本。"""
    s = raw.strip()
    if "```" in s:
        m = re.search(r"```[a-zA-Z]*\s*\n(.*?)```", s, re.DOTALL)
        if m:
            s = m.group(1)
    return s.strip() + "\n"


def extract_plan(text: str) -> Optional[dict]:
    s = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.IGNORECASE).strip()
    i, j = s.find("{"), s.rfind("}")
    if i < 0 or j <= i:
        return None
    try:
        obj = json.loads(s[i:j + 1])
    except Exception:  # noqa: BLE001
        return None
    if isinstance(obj, dict) and isinstance(obj.get("steps"), list) and obj["steps"]:
        return obj
    return None


def normalize_plan_interaction(plan: dict, interactive: bool) -> dict:
    """让展示给用户的方案文字与互动开关一致，防止弱模型残留相反措辞。"""
    out = dict(plan)
    out["steps"] = [dict(step) for step in (plan.get("steps") or [])]
    if interactive:
        if not str(out.get("interaction", "")).strip():
            out["interaction"] = ("根据本题核心变量选择一个有效控件，明确范围、初值、"
                                  "控制对象及操作后可观察的变化")
        return out

    out["interaction"] = ""
    replacements = (
        ("随滑杆变化", "按固定参数"),
        ("拖动滑杆", "查看固定参数"),
        ("滑杆", "固定参数"),
        ("可拖动的点", "固定点"),
        ("可拖动点", "固定点"),
        ("动点", "固定点"),
        ("动态", "静态"),
        ("动画", "静态示意"),
        ("勾选框", "固定显示"),
        ("互动", "静态"),
        ("交互", "静态"),
    )
    for step in out["steps"]:
        for key in ("teaches", "shows"):
            value = str(step.get(key, ""))
            for old, new in replacements:
                value = value.replace(old, new)
            value = re.sub(
                r"固定参数\s*([A-Za-z][A-Za-z0-9_]*)，?取值范围.*?初始值为\s*([^，。；]+)",
                r"固定参数 \1=\2", value)
            step[key] = value
    return out


def format_plan(plan: dict) -> str:
    b = plan.get("brief") or {}
    lines: list[str] = []
    if b.get("topic"):
        lines.append(f"主题：{b['topic']}" + (f"（面向 {b['audience']}）" if b.get("audience") else ""))
    if b.get("core_claim"):
        lines.append(f"核心：{b['core_claim']}")
    lines.append("步骤：")
    for idx, st in enumerate(plan.get("steps") or [], 1):
        sid = st.get("id") or f"step_{idx:02d}"
        lines.append(f"{idx}. [{sid}｜画：{st.get('teaches', '')}] {st.get('shows', '')}")
    if plan.get("interaction"):
        lines.append(f"交互：{plan['interaction']}")
    return "\n".join(lines)


_STEP_RE = re.compile(r"^[ \t]*#\s*step[_ ]?0*(\d+)\s*[:：|｜]?\s*(.*?)\s*$", re.IGNORECASE)


def split_steps(script: str) -> list[dict]:
    """按 `# step_NN` 注释把脚本切段。返回 [{idx,label,code}]；无标记返回 []。"""
    lines = script.splitlines()
    marks = []
    for i, ln in enumerate(lines):
        m = _STEP_RE.match(ln)
        if m:
            marks.append((i, int(m.group(1)), (m.group(2) or "").strip()))
    if not marks:
        return []
    steps = []
    for k, (ln_no, idx, label) in enumerate(marks):
        start = 0 if k == 0 else ln_no
        end = marks[k + 1][0] if k + 1 < len(marks) else len(lines)
        steps.append({"idx": idx, "label": label or f"第{idx}步",
                      "code": "\n".join(lines[start:end]).strip("\n")})
    return steps


def fix_prompt(script: str, issues: list[str], intent: str, plan: str = "",
               objects: Optional[list[str]] = None) -> str:
    plan_block = f"教学步骤计划（必须完整保留）：\n{plan}\n\n" if plan else ""
    obj_block = ("当前已成功创建的对象：" + "、".join(objects) + "\n\n") if objects else ""
    issue_text = "\n".join(f"- {m}" for m in issues)
    return (f"原始意图：{intent}\n\n{plan_block}{obj_block}"
            f"当前脚本：\n{script}\n\n发现的问题：\n{issue_text}\n\n"
            f"请返回修复后的完整脚本。")


# 老师明确想整段重做的措辞
WANTS_REMAKE = re.compile(r"重做|重新生成|重新做|重新制作|从头(做|来|生成)|整段重|换个思路重|彻底重")
