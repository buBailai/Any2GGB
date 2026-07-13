"""LLM 抽象层（BYO-Key，OpenAI 兼容）。搬自 Any2Manim（超时/重试/max_tokens 经验保留）。

- OpenAILLM：真实厂商/自定义端点。
- MockLLM：无 Key 时的「演示模式」，返回亲测可跑的 GGB 命令脚本，
  让整条管线没接 Key 也能端到端出课件。

task 取值：plan | scriptgen | fix | edit —— 真实模型忽略它，Mock 据它造输出。
"""
from __future__ import annotations

import asyncio
import json
import re
from typing import Any, Optional

import httpx


class LLMError(Exception):
    pass


_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


def _strip_think(text: str) -> str:
    return _THINK_RE.sub("", text).strip()


class BaseLLM:
    name = "base"
    demo = False
    #: 端点是否可能支持视觉输入（真实端点默认认为可以，由用户配置的模型决定）
    vision = False

    def complete(self, system: str, user: str, *, task: str = "scriptgen",
                 temperature: float = 0.2, images: Optional[list[str]] = None) -> str:
        raise NotImplementedError

    async def acomplete(self, system: str, user: str, *, task: str = "scriptgen",
                        temperature: float = 0.2,
                        images: Optional[list[str]] = None) -> str:
        """异步入口；真实网络实现会覆盖它以支持取消正在进行的 HTTP 请求。"""
        return await asyncio.to_thread(
            self.complete, system, user, task=task,
            temperature=temperature, images=images,
        )


# ── 真实端点 ────────────────────────────────────────────────
def _as_data_url(img: str) -> str:
    """把裸 base64 或 dataURL 统一成 data URL（默认按 PNG）。"""
    s = (img or "").strip()
    if s.startswith("data:"):
        return s
    return f"data:image/png;base64,{s}"


class OpenAILLM(BaseLLM):
    vision = True   # OpenAI 兼容端点，是否真支持视觉取决于用户配的模型

    def __init__(self, base_url: str, api_key: str, model: str):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key or "no-key"
        self.model = model
        self.name = model

    def _endpoint(self) -> str:
        b = self.base_url
        if b.endswith("/chat/completions"):
            return b
        return f"{b}/chat/completions"

    MAX_TOKENS = 8192
    TIMEOUT = httpx.Timeout(300.0, connect=15.0)

    def complete(self, system: str, user: str, *, task: str = "scriptgen",
                 temperature: float = 0.2, images: Optional[list[str]] = None) -> str:
        url = self._endpoint()
        if images:      # 视觉输入：user content 走 OpenAI 多模态数组格式
            user_content: Any = [{"type": "text", "text": user}]
            for img in images[:4]:
                user_content.append({"type": "image_url",
                                     "image_url": {"url": _as_data_url(img)}})
        else:
            user_content = user
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user_content},
            ],
            "temperature": temperature,
            "max_tokens": self.MAX_TOKENS,
        }
        headers = {"Authorization": f"Bearer {self.api_key}"}
        last_err: Exception | None = None
        for attempt in range(2):          # 超时/5xx/断连 自动重试 1 次
            try:
                with httpx.Client(timeout=self.TIMEOUT) as cli:
                    r = cli.post(url, json=payload, headers=headers)
                    if r.status_code == 400 and "max_tokens" in payload \
                            and "max_tokens" in r.text:
                        payload.pop("max_tokens")
                        r = cli.post(url, json=payload, headers=headers)
                    r.raise_for_status()
                    data = r.json()
                return _strip_think(data["choices"][0]["message"]["content"])
            except httpx.HTTPStatusError as e:
                if e.response.status_code >= 500 and attempt == 0:
                    last_err = e
                    continue
                raise LLMError(f"LLM 接口返回 {e.response.status_code}: {e.response.text[:200]}")
            except (httpx.TimeoutException, httpx.TransportError) as e:
                last_err = e
                if attempt == 0:
                    continue
            except Exception as e:  # noqa: BLE001
                raise LLMError(f"LLM 调用失败：{e}")
        raise LLMError(f"LLM 调用失败（已重试）：{last_err}")

    async def acomplete(self, system: str, user: str, *, task: str = "scriptgen",
                        temperature: float = 0.2,
                        images: Optional[list[str]] = None) -> str:
        """可取消的真实模型请求；取消生成会关闭当前 HTTP 连接。"""
        url = self._endpoint()
        if images:
            user_content: Any = [{"type": "text", "text": user}]
            for img in images[:4]:
                user_content.append({"type": "image_url",
                                     "image_url": {"url": _as_data_url(img)}})
        else:
            user_content = user
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user_content},
            ],
            "temperature": temperature,
            "max_tokens": self.MAX_TOKENS,
        }
        headers = {"Authorization": f"Bearer {self.api_key}"}
        last_err: Exception | None = None
        for attempt in range(2):
            try:
                async with httpx.AsyncClient(timeout=self.TIMEOUT) as cli:
                    response = await cli.post(url, json=payload, headers=headers)
                    if response.status_code == 400 and "max_tokens" in payload \
                            and "max_tokens" in response.text:
                        payload.pop("max_tokens")
                        response = await cli.post(url, json=payload, headers=headers)
                    response.raise_for_status()
                    data = response.json()
                return _strip_think(data["choices"][0]["message"]["content"])
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code >= 500 and attempt == 0:
                    last_err = exc
                    continue
                raise LLMError(
                    f"LLM 接口返回 {exc.response.status_code}: {exc.response.text[:200]}"
                )
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_err = exc
                if attempt == 0:
                    continue
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                raise LLMError(f"LLM 调用失败：{exc}")
        raise LLMError(f"LLM 调用失败（已重试）：{last_err}")


# ── 演示模式（无 Key）：亲测可跑的 GGB 脚本 ───────────────────
_PYTHAGORAS = """# perspective: 2d
# step_01：画出直角三角形
A=(0,0)
B=(4,0)
C=(4,3)
tri=Polygon(A,B,C)
SetColor(tri,91,91,214)
SetCaption(A,"A")
SetCaption(B,"B")
SetCaption(C,"C")
# step_02：标注三边
sa=Segment(A,B)
sb=Segment(B,C)
sc=Segment(A,C)
ShowLabel(sa,false)
ShowLabel(sb,false)
ShowLabel(sc,false)
ta=Text("a",Midpoint(sa)+(0,-0.4))
tb=Text("b",Midpoint(sb)+(0.25,0))
tc=Text("c",Midpoint(sc)+(-0.35,0.3))
# step_03：勾股定理公式与结论
t1=Text("a^2+b^2=c^2",(0.3,4),true,true)
# view: -1.5 -1.5 6 5"""

_PYTHAGORAS_INTERACTIVE = """# perspective: 2d
# view: -1.5 -1.5 7 6
# step_01：用滑杆控制一条直角边
a=Slider(2,6,0.5)
SetValue(a,4)
A=(0,0)
B=(a,0)
C=(0,3)
tri=Polygon(A,B,C)
# step_02：动态边长与公式
sa=Segment(A,B)
sb=Segment(A,C)
sc=Segment(B,C)
t1=Text("拖动 a，观察三边关系",(0.2,5.2))"""

_PARABOLA = """# perspective: 2d
# step_01：坐标视窗与滑杆
a=Slider(-2,2,0.1)
SetValue(a,1)
# view: -5 -3 5 6
# step_02：带参数的抛物线（拖动滑杆看开口变化）
f(x)=a*x^2
SetColor(f,63,182,198)
SetLineThickness(f,5)
# step_03：顶点与说明
V=(0,0)
SetCaption(V,"顶点")
t1=Text("拖动滑杆 a，观察抛物线开口方向与宽窄",(-4.5,5.2))"""

_CIRCLE = """# perspective: 2d
# step_01：圆心与圆
O=(0,0)
c=Circle(O,3)
SetColor(c,91,91,214)
SetCaption(O,"圆心")
# step_02：圆上动点与半径（可拖动）
P=Point(c)
r=Segment(O,P)
SetColor(r,230,120,60)
t1=Text("半径 r",Midpoint(r)+(0.2,0.3))
# step_03：面积公式
t2=Text("S=\\pi r^2",(-4.2,3.6),true,true)
# view: -5 -4.2 5 4.2
StartAnimation(P)"""

_DEFAULT = """# perspective: 2d
# step_01：视窗与单位圆
# view: -3 -2.5 3 3
c=Circle((0,0),2)
SetColor(c,60,90,166)
SetLineThickness(c,4)
# step_02：圆上一点与半径
P=Point(c)
r=Segment((0,0),P)
SetColor(r,230,120,60)
t1=Text("半径 r",Midpoint(r)+(0.2,0.3))"""

_CUBE = """# perspective: 3d
# view3d: -1 -1 -1 5 5 5
# step_01：建立正方体
A=(0,0,0)
B=(3,0,0)
cu=Cube(A,B)
# step_02：标出一条体对角线
diag=Segment(A,G)
SetLineThickness(diag,5)"""

_PARABOLA_STATIC = """# perspective: 2d
# view: -5 -3 5 6
# step_01：静态抛物线
f(x)=x^2
SetLineThickness(f,5)
# step_02：顶点与说明
V=(0,0)
SetCaption(V,"顶点")
t1=Text("y=x^2",(-4.5,5.2),true,true)"""

_CIRCLE_STATIC = """# perspective: 2d
# view: -5 -4.2 5 4.2
# step_01：圆心与圆
O=(0,0)
c=Circle(O,3)
SetCaption(O,"圆心")
# step_02：固定半径与公式
P=(3,0)
r=Segment(O,P)
t1=Text("半径 r",(1.2,0.35))
t2=Text("S=\\pi r^2",(-4.2,3.6),true,true)"""

_CUBE_INTERACTIVE = """# perspective: 3d
# view3d: -1 -1 -1 6 6 6
# step_01：用滑杆控制正方体棱长
a=Slider(1,5,0.5)
SetValue(a,3)
A=(0,0,0)
B=(a,0,0)
cu=Cube(A,B)
# step_02：动态体对角线
diag=Segment(A,G)
SetLineThickness(diag,5)"""


class MockLLM(BaseLLM):
    name = "演示模式"
    demo = True

    def complete(self, system: str, user: str, *, task: str = "scriptgen",
                 temperature: float = 0.2, images: Optional[list[str]] = None) -> str:
        text = user
        interactive = "【互动模式】" in system
        _clean = text.strip()
        for _ in range(3):   # 可能叠了多层前缀（老师的需求：图形主题：…）
            _new = re.sub(r"^(老师的需求|需求|图形主题|绘图方案|原始意图)[:：]\s*", "", _clean)
            if _new == _clean:
                break
            _clean = _new
        title = (re.split(r"[。.\n]", _clean)[0] or "演示配图")[:18]

        if task == "edit":
            return ""     # 演示模式不做定向编辑 → 上层降级重生成

        if task == "plan":
            return json.dumps({
                "brief": {"topic": title, "audience": "",
                          "core_claim": f"为「{title}」画一张清晰的题目配图"},
                "steps": [
                    {"id": "step_01", "teaches": "画主体图形并设定视窗"},
                    {"id": "step_02", "teaches": "标注关键点/边/角"},
                    {"id": "step_03", "teaches": "补充公式或说明文本"},
                ],
                "interaction": ("使用一个控件控制核心变量，观察图形随参数变化"
                                if interactive else ""),
            }, ensure_ascii=False)

        if task == "fix":
            if "perspective: 3d" in user.lower():
                return _CUBE_INTERACTIVE if interactive else _CUBE
            return _DEFAULT if interactive else _PYTHAGORAS

        if "输出空间：3D" in system:
            return _CUBE_INTERACTIVE if interactive else _CUBE

        if any(k in text for k in ("勾股", "直角三角", "pythag")):
            return _PYTHAGORAS_INTERACTIVE if interactive else _PYTHAGORAS
        if any(k in text for k in ("函数", "抛物", "图像", "parabola", "x^2", "x²")):
            return _PARABOLA if interactive else _PARABOLA_STATIC
        if any(k in text for k in ("圆", "circle")):
            return _CIRCLE if interactive else _CIRCLE_STATIC
        return _DEFAULT if interactive else _PYTHAGORAS

    async def acomplete(self, system: str, user: str, *, task: str = "scriptgen",
                        temperature: float = 0.2,
                        images: Optional[list[str]] = None) -> str:
        return self.complete(system, user, task=task, temperature=temperature, images=images)


def from_config(cfg: Optional[dict]) -> BaseLLM:
    """据 data/config.json 构造 LLM；缺关键项 → 演示模式。"""
    if not cfg or not cfg.get("base_url") or not cfg.get("model"):
        return MockLLM()
    from . import providers
    preset = providers.get(cfg.get("provider", "")) or {}
    needs_key = preset.get("needs_key", True)
    if needs_key and not cfg.get("api_key"):
        return MockLLM()
    return OpenAILLM(cfg["base_url"], cfg.get("api_key", ""), cfg["model"])
