"""Any2GGB 后端服务（FastAPI + SSE）。

生成回路（与 Any2Manim 的关键差异——执行在前端 GGB 引擎）：
  POST /message → 后台任务：规划→脚本→lint 预检 → SSE 推 script_ready
  → 前端逐行执行 → POST /verify 回报（成功带缩略图/快照；失败带失败行+对象表）
  → 成功落版本；失败喂 LLM 修一版再推（有界 HEAL_MAX_ROUNDS，保住最后一版脚本）。
"""
from __future__ import annotations

import asyncio
import base64
import binascii
import json
from contextlib import asynccontextmanager
from typing import Any, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import (config, db, edits, engine, examples, generation_cache, lint, modes,
               providers, solid3d, store, style2d, update)
from .llm import LLMError, from_config


# ── SSE broker ──────────────────────────────────────────────
class Broker:
    def __init__(self) -> None:
        self.subs: dict[str, list[asyncio.Queue]] = {}

    def subscribe(self, pid: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self.subs.setdefault(pid, []).append(q)
        return q

    def unsubscribe(self, pid: str, q: asyncio.Queue) -> None:
        try:
            self.subs.get(pid, []).remove(q)
        except ValueError:
            pass

    async def publish(self, pid: str, data: dict) -> None:
        for q in list(self.subs.get(pid, [])):
            q.put_nowait(data)


broker = Broker()

# 待前端回报的执行结果：(pid, seq) → Future[dict]
_pending_verify: dict[tuple[str, int], asyncio.Future] = {}
# 正在生成的项目（防同项目并发生成）
_generating: set[str] = set()
# 项目 → 当前生成任务。保留句柄后才能从刷新后的页面主动取消。
_generation_tasks: dict[str, asyncio.Task] = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    config.ensure_dirs()
    print(f"[Any2GGB] v{config.app_version()} · http://0.0.0.0:{config.PORT}", flush=True)
    yield


app = FastAPI(title="Any2GGB", lifespan=lifespan)
app.include_router(update.router)


# ── 基础信息 ────────────────────────────────────────────────
@app.get("/api/providers")
def get_providers():
    return providers.public_list()


@app.get("/api/examples")
def get_examples():
    return examples.EXAMPLES


@app.get("/api/modes")
def get_modes():
    return {"modes": modes.public_list(), "spaces": modes.public_spaces(),
            "default_mode": "figure", "default_space": "2d",
            "default_interactive": False}


_changelog_cache: dict = {"mtime": 0.0, "data": None}


@app.get("/api/changelog")
def get_changelog():
    p = config.APP_DIR / "CHANGELOG.md"
    try:
        mt = p.stat().st_mtime
    except OSError:
        return {"version": config.app_version(), "markdown": ""}
    if _changelog_cache["data"] is None or mt != _changelog_cache["mtime"]:
        _changelog_cache.update(mtime=mt,
                                data={"version": config.app_version(),
                                      "markdown": p.read_text(encoding="utf-8")})
    return _changelog_cache["data"]


# ── API 配置 ────────────────────────────────────────────────
class ConfigIn(BaseModel):
    provider: str = ""
    base_url: str = ""
    api_key: str = ""
    model: str = ""


@app.get("/api/config")
def get_config():
    c = config.read_config()
    active = c.get("active", "")
    flat = config.provider_flat(active) if active else {}
    provs = {p: {"base_url": v.get("base_url", ""), "model": v.get("model", ""),
                 "has_key": bool(v.get("api_key"))}
             for p, v in c.get("providers", {}).items()}
    return {"active": active, "base_url": flat.get("base_url", ""),
            "model": flat.get("model", ""), "has_key": bool(flat.get("api_key")),
            "providers": provs, "demo": from_config(config.active_flat()).demo}


@app.post("/api/config")
def set_config(c: ConfigIn):
    config.save_provider(c.provider, c.base_url.strip(), c.api_key.strip(), c.model.strip())
    return {"ok": True, "demo": from_config(config.active_flat()).demo}


@app.post("/api/config/test")
def test_config(c: ConfigIn):
    cfg = {"provider": c.provider, "base_url": c.base_url.strip(),
           "api_key": c.api_key.strip(), "model": c.model.strip()}
    if not cfg["api_key"]:      # 未填 Key 时用已存的（改模型测连通不必重贴 Key）
        cfg["api_key"] = config.provider_flat(c.provider).get("api_key", "")
    llm = from_config(cfg)
    if llm.demo:
        return {"ok": False, "msg": "配置不完整（缺地址/模型/Key）"}
    try:
        r = llm.complete("你是回声机。", "只回复两个字：正常", task="plan", temperature=0.0)
        return {"ok": True, "msg": f"连通正常：{r[:40]}"}
    except LLMError as e:
        return {"ok": False, "msg": str(e)[:200]}


# ── 项目 CRUD ───────────────────────────────────────────────
class ProjectIn(BaseModel):
    title: str = "未命名配图"
    subject: str = ""


class RenameIn(BaseModel):
    title: str


class ArchiveIn(BaseModel):
    archived: bool = True


@app.get("/api/projects")
def list_projects(archived: bool = False):
    return store.list_projects(archived)


@app.post("/api/projects")
def create_project(p: ProjectIn):
    return store.create_project(p.title.strip() or "未命名配图", p.subject)


@app.get("/api/projects/{pid}")
def get_project(pid: str):
    proj = store.get_project(pid)
    if not proj:
        raise HTTPException(404)
    versions = store.list_versions(pid)
    has_pending = any(version.get("status") == "pending" for version in versions)
    return {"project": proj, "versions": versions,
            "messages": store.list_messages(pid),
            "generating": pid in _generating, "has_pending": has_pending}


@app.post("/api/projects/{pid}/rename")
def rename_project(pid: str, r: RenameIn):
    store.rename_project(pid, r.title.strip()[:60] or "未命名配图")
    return {"ok": True}


@app.post("/api/projects/{pid}/archive")
def archive_project(pid: str, a: ArchiveIn):
    store.set_archived(pid, a.archived)
    return {"ok": True}


@app.delete("/api/projects/{pid}")
def delete_project(pid: str):
    store.delete_project(pid)
    return {"ok": True}


@app.get("/api/projects/{pid}/version/{seq}")
def get_version(pid: str, seq: int):
    v = store.get_version(pid, seq)
    if not v:
        raise HTTPException(404)
    return v


class RevertIn(BaseModel):
    seq: int


@app.post("/api/projects/{pid}/revert")
def revert(pid: str, r: RevertIn):
    v = store.get_version(pid, r.seq)
    if not v or not v.get("script"):
        raise HTTPException(404, "该版本没有可用脚本")
    store.set_current(pid, r.seq)
    store.add_message(pid, "system", f"已回退到第 {r.seq} 版", r.seq)
    return {"ok": True, "version": v}


# ── SSE ─────────────────────────────────────────────────────
@app.get("/api/events/{pid}")
async def events(pid: str):
    q = broker.subscribe(pid)

    async def gen():
        try:
            while True:
                try:
                    data = await asyncio.wait_for(q.get(), timeout=15)
                    yield f"data: {json.dumps(data, ensure_ascii=False)}\n\n"
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
        finally:
            broker.unsubscribe(pid, q)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


# ── 生成回路 ────────────────────────────────────────────────
class Attachment(BaseModel):
    name: str = ""
    kind: str = "image"          # image | text
    data: str = ""               # image: dataURL 或裸 base64；text: 文件文本内容


class MessageIn(BaseModel):
    text: str
    attachments: list[Attachment] = []
    mode: str = "figure"
    space: str = "2d"
    interactive: bool = False


class VerifyIn(BaseModel):
    seq: int
    ok: bool
    failures: list[dict] = []      # [{line:int, cmd:str}]
    objects: list[str] = []        # 已成功创建的对象名
    png_base64: str = ""           # 成功时的缩略图
    ggb_base64: str = ""           # 成功时的 .ggb 快照


def _save_upload_image(pid: str, seq_hint: str, idx: int, data_url: str) -> str:
    """把上传的参考图落盘，返回相对路径（失败返回空串）。"""
    try:
        b64 = data_url.split(",", 1)[1] if data_url.startswith("data:") else data_url
        raw = base64.b64decode(b64)
    except (binascii.Error, IndexError, ValueError):
        return ""
    pdir = config.project_dir(pid) / "uploads"
    pdir.mkdir(parents=True, exist_ok=True)
    f = pdir / f"{seq_hint}_{idx}.png"
    try:
        f.write_bytes(raw)
    except OSError:
        return ""
    return f"projects/{pid}/uploads/{f.name}"


@app.post("/api/projects/{pid}/message")
async def post_message(pid: str, m: MessageIn):
    proj = store.get_project(pid)
    if not proj:
        raise HTTPException(404)
    if proj.get("archived"):
        raise HTTPException(400, "项目已归档（只读）")
    if pid in _generating:
        raise HTTPException(409, "该项目正在生成中，请稍候")
    text = m.text.strip()

    # 拆分附件：图片走视觉输入，文本文件并入需求描述
    images: list[str] = []
    file_texts: list[str] = []
    for a in m.attachments[:6]:
        if a.kind == "image" and a.data:
            images.append(a.data)
        elif a.kind == "text" and a.data:
            file_texts.append(f"参考文件《{a.name or '未命名'}》内容：\n{a.data.strip()[:8000]}")

    if not text and not images and not file_texts:
        raise HTTPException(400, "内容为空")

    # 组装喂给模型的完整需求
    prompt = text or ("请根据附图还原/绘制配图" if images else "请根据参考资料绘制配图")
    if file_texts:
        prompt = prompt + "\n\n" + "\n\n".join(file_texts)

    # 落盘参考图 + 在对话里留痕
    import time as _t
    stamp = str(int(_t.time()))
    saved = [p for i, d in enumerate(images) if (p := _save_upload_image(pid, stamp, i, d))]
    marker = text
    if images:
        marker += f"　［附 {len(images)} 张参考图］"
    if file_texts:
        marker += f"　［附 {len(file_texts)} 个文件］"
    store.add_message(pid, "user", marker.strip() or "（仅附件）")

    mode = modes.normalize_mode(m.mode)
    space = modes.normalize_space(m.space)
    _generating.add(pid)
    task = asyncio.create_task(_run_generation(
        pid, prompt, images=images, mode=mode, space=space,
        interactive=m.interactive,
    ))
    _generation_tasks[pid] = task
    return {"ok": True, "saved_images": saved, "mode": mode, "space": space,
            "interactive": m.interactive}


@app.post("/api/projects/{pid}/cancel")
async def cancel_generation(pid: str):
    if not store.get_project(pid):
        raise HTTPException(404)

    task = _generation_tasks.get(pid)
    had_live_task = bool(task and not task.done())
    if had_live_task:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    # 服务重启或页面异常可能留下没有内存任务的 pending 记录，也一并收口。
    stale_seqs = store.cancel_pending_versions(pid)
    _generating.discard(pid)
    _generation_tasks.pop(pid, None)
    if stale_seqs:
        for seq in stale_seqs:
            store.add_message(pid, "system", f"已手动停止第 {seq} 版生成", seq)
        await broker.publish(pid, {"type": "version_cancelled", "seq": stale_seqs[-1]})

    return {"ok": True, "cancelled": had_live_task or bool(stale_seqs),
            "seqs": stale_seqs}


@app.post("/api/projects/{pid}/verify")
async def post_verify(pid: str, v: VerifyIn):
    fut = _pending_verify.get((pid, v.seq))
    if fut is None or fut.done():
        return {"ok": False, "msg": "没有等待中的验证请求（可能已超时）"}
    fut.set_result(v.model_dump())
    return {"ok": True}


def _save_media(pid: str, seq: int, png_b64: str, ggb_b64: str) -> tuple[str, str]:
    """把前端回传的缩略图/.ggb 快照落盘，返回 (thumb_rel, ggb_rel)。"""
    pdir = config.project_dir(pid)
    pdir.mkdir(parents=True, exist_ok=True)
    thumb_rel = ggb_rel = ""
    try:
        if png_b64:
            f = pdir / f"v{seq}.png"
            f.write_bytes(base64.b64decode(png_b64))
            thumb_rel = f"projects/{pid}/v{seq}.png"
        if ggb_b64:
            f = pdir / f"v{seq}.ggb"
            f.write_bytes(base64.b64decode(ggb_b64))
            ggb_rel = f"projects/{pid}/v{seq}.ggb"
    except (binascii.Error, OSError):
        pass
    return thumb_rel, ggb_rel


class ManualVersionIn(BaseModel):
    script: str
    plan: str = ""
    png_base64: str = ""
    ggb_base64: str = ""


@app.post("/api/projects/{pid}/manual-version")
def save_manual_version(pid: str, body: ManualVersionIn):
    proj = store.get_project(pid)
    if not proj:
        raise HTTPException(404)
    if proj.get("archived"):
        raise HTTPException(400, "项目已归档（只读）")
    if pid in _generating:
        raise HTTPException(409, "该项目正在生成中，请稍候")
    script = body.script.strip()
    if not script:
        raise HTTPException(400, "脚本为空")
    if engine.script_space(script) == "3d":
        script = solid3d.normalize_script(script)
    else:
        script = style2d.normalize_script(script)
    fixed, issues = lint.preflight(script)
    if issues:
        raise HTTPException(400, "脚本预检未通过：" + "；".join(i["msg"] for i in issues[:3]))
    seq = store.next_seq(pid)
    store.create_version(pid, seq, "手动编辑脚本")
    thumb, ggb = _save_media(pid, seq, body.png_base64, body.ggb_base64)
    store.finish_version(pid, seq, status="ok", script=fixed.rstrip() + "\n",
                         plan=body.plan.strip(), thumb_path=thumb, ggb_path=ggb)
    store.add_message(pid, "system", f"已保存手动修改为第 {seq} 版", seq)
    return {"ok": True, "seq": seq, "thumb": f"/media/{thumb}" if thumb else ""}


async def _wait_verify(pid: str, seq: int) -> Optional[dict]:
    fut: asyncio.Future = asyncio.get_event_loop().create_future()
    _pending_verify[(pid, seq)] = fut
    try:
        return await asyncio.wait_for(fut, timeout=config.VERIFY_TIMEOUT)
    except asyncio.TimeoutError:
        return None
    finally:
        _pending_verify.pop((pid, seq), None)


async def _run_generation(pid: str, prompt: str, images: Optional[list[str]] = None,
                          mode: str = "figure", space: str = "2d",
                          interactive: bool = False) -> None:
    seq = store.next_seq(pid)
    store.create_version(pid, seq, prompt)
    images = images or []

    async def emit(ev: str, **data: Any) -> None:
        await broker.publish(pid, {"type": ev, "seq": seq, **data})

    active_llm_config = config.active_flat()
    llm = from_config(active_llm_config)
    plan_text = ""
    script = ""
    rounds = 0
    cache_key = ""
    used_cache = False
    edited = False
    # 有参考图但当前端点不支持视觉时，提示但仍按文字需求尝试
    vision_note = ""
    if images and not getattr(llm, "vision", False) and not llm.demo:
        vision_note = "（注意：当前模型可能不支持读图，若还原效果差请换用支持视觉的模型）"
    if images:
        prompt = (prompt + "\n\n随附参考图，请仔细观察图中的点、线段、角、标注文字与整体比例，"
                  "用 GeoGebra 命令尽量忠实地还原成一张干净的题目配图。")
    try:
        await emit("version_start", demo=llm.demo, has_images=bool(images), mode=mode,
                   space=space, interactive=interactive)
        if vision_note:
            await emit("notice", text=vision_note)
        if images and llm.demo:
            await emit("notice", text="演示模式无法读图，将忽略参考图按文字需求出图；配置支持视觉的模型后可真正复刻。")
        prior = store.latest_script(pid)
        base_prompt = store.first_user_prompt(pid)

        # 新项目的完整生成可按题目/附件/选项/模型精确复用。复用脚本仍必须经过
        # 当前浏览器 GeoGebra 执行验证，避免把过期或损坏结果直接交给用户。
        if not prior and not engine.WANTS_REMAKE.search(prompt):
            cache_key = generation_cache.make_key(
                prompt=prompt, images=images, mode=mode, space=space,
                interactive=interactive, llm_name=llm.name,
                llm_config=active_llm_config,
            )
            cached = store.get_generation_cache(cache_key)
            if cached:
                used_cache = True
                plan_text = cached.get("plan", "")
                script = cached.get("script", "")
                await emit("cache_hit", hit_count=int(cached.get("hit_count", 0)) + 1)
                if plan_text:
                    await emit("plan_ready", text=plan_text)

        # ── 定向编辑路径：有旧脚本且不是明确要求重做（附参考图时一律走整段还原）──
        same_space = not prior or engine.script_space(prior) == space
        same_interaction = not prior or engine.script_is_interactive(prior) == interactive
        if (not used_cache and prior and same_space and same_interaction and not images
                and not engine.WANTS_REMAKE.search(prompt) and not llm.demo):
            await emit("editing")
            try:
                raw = await llm.acomplete(
                    engine.sys_edit(mode, space, interactive),
                    f"当前脚本：\n{prior}\n\n修改要求：{prompt}",
                    task="edit", temperature=0.2,
                )
                blocks = edits.parse_blocks(raw)
                if blocks:
                    er = edits.apply_blocks(prior, blocks)
                    if er.applied > 0:
                        script = er.code
                        edited = True
                        await emit("edited", applied=er.applied, blocks=er.blocks)
            except LLMError:
                pass

        # ── 整段生成路径 ──
        if not used_cache and not edited:
            if prior:
                await emit("regenerating")
            await emit("planning")
            gen_desc = (f"图形主题：{base_prompt}\n\n请在上一版基础上做这个调整：{prompt}"
                        if (prior and base_prompt) else prompt)
            try:
                raw_plan = await llm.acomplete(
                    engine.sys_plan(mode, space, interactive),
                    f"老师的需求：{gen_desc}", task="plan",
                    temperature=0.4, images=images,
                )
            except LLMError as e:
                await _fail(pid, seq, emit, f"LLM 调用失败：{e}", script="", plan="")
                return
            plan_dict = engine.extract_plan(raw_plan)
            if plan_dict:
                plan_dict = engine.normalize_plan_interaction(plan_dict, interactive)
            plan_text = engine.format_plan(plan_dict) if plan_dict else raw_plan.strip()
            await emit("plan_ready", text=plan_text)

            await emit("generating")
            try:
                raw = await llm.acomplete(
                    engine.sys_scriptgen(mode, space, interactive),
                    f"需求：{gen_desc}\n\n绘图方案：\n{plan_text}",
                    task="scriptgen", temperature=0.2, images=images,
                )
            except LLMError as e:
                await _fail(pid, seq, emit, f"LLM 调用失败：{e}", script="", plan=plan_text)
                return
            script = engine.extract_script(raw)

        # ── 自愈回路：lint 预检 + 前端执行验证，有界修复 ──
        last_fail_summary = ""
        while True:
            script = engine.ensure_space_directive(script, space)
            if space == "3d":
                script = solid3d.normalize_script(script)
            else:
                script = style2d.normalize_script(script)
            script, lint_issues = lint.preflight(script)
            interaction_issues = engine.interaction_issues(script, interactive)
            issues = lint_issues + interaction_issues
            if issues and rounds < config.HEAL_MAX_ROUNDS:
                rounds += 1
                await emit("healing", round=rounds, reason="静态预检发现必挂问题")
                try:
                    raw = await llm.acomplete(
                        engine.sys_fix(mode, space, interactive),
                        engine.fix_prompt(script, [i["msg"] for i in issues], prompt, plan_text),
                        task="fix", temperature=0.3,
                    )
                    fixed = engine.extract_script(raw)
                    if fixed.strip():
                        script = fixed
                    continue
                except LLMError:
                    pass    # 修不了就带病送前端试一把（创建类问题前端还能兜）

            if interaction_issues:
                await _fail(pid, seq, emit,
                            "模型多次修复后仍未遵守互动开关，请换一个模型或重试。",
                            script=script, plan=plan_text, rounds=rounds)
                return

            await emit("script_ready", script=script, round=rounds)
            result = await _wait_verify(pid, seq)
            if result is None:
                await _fail(pid, seq, emit, "等待预览执行结果超时（页面可能已关闭）",
                            script=script, plan=plan_text, rounds=rounds)
                return
            if result.get("ok"):
                thumb, ggb = _save_media(pid, seq, result.get("png_base64", ""),
                                         result.get("ggb_base64", ""))
                store.finish_version(pid, seq, status="ok", script=script, plan=plan_text,
                                     heal_rounds=rounds, thumb_path=thumb, ggb_path=ggb)
                store.add_message(pid, "assistant",
                                  f"第 {seq} 版图形已生成" + (f"（自动修正 {rounds} 轮）" if rounds else ""),
                                  seq)
                if cache_key:
                    if used_cache:
                        store.mark_generation_cache_used(cache_key)
                    elif not edited:
                        store.put_generation_cache(cache_key, plan_text, script)
                await emit("version_done", thumb=f"/media/{thumb}" if thumb else "")
                return

            fails = result.get("failures", [])[:8]
            last_fail_summary = "; ".join(f"L{f.get('line')}:{f.get('cmd', '')[:40]}" for f in fails)
            if rounds >= config.HEAL_MAX_ROUNDS:
                await _fail(pid, seq, emit,
                            "多次修复仍有执行失败的命令。可以换个说法重述，或在脚本 tab 手动调。",
                            script=script, plan=plan_text, rounds=rounds,
                            fail_lines=last_fail_summary)
                return
            rounds += 1
            await emit("healing", round=rounds, reason=f"{len(fails)} 行执行失败")
            msgs = [f"第 {f.get('line')} 行执行失败：{f.get('cmd', '')}" for f in fails]
            try:
                raw = await llm.acomplete(
                    engine.sys_fix(mode, space, interactive),
                    engine.fix_prompt(script, msgs, prompt, plan_text,
                                      objects=result.get("objects", [])[:40]),
                    task="fix", temperature=0.3,
                )
                fixed = engine.extract_script(raw)
                if fixed.strip():
                    script = fixed
            except LLMError as e:
                await _fail(pid, seq, emit, f"修复调用失败：{e}", script=script,
                            plan=plan_text, rounds=rounds, fail_lines=last_fail_summary)
                return
    except asyncio.CancelledError:
        cancelled = store.cancel_pending_versions(pid, seq)
        if cancelled:
            store.add_message(pid, "system", f"已手动停止第 {seq} 版生成", seq)
            await emit("version_cancelled")
        return
    except Exception as e:  # noqa: BLE001 —— 兜底：任何意外不让任务静默消失
        await _fail(pid, seq, emit, f"内部错误：{e}", script=script, plan=plan_text,
                    rounds=rounds)
    finally:
        _generating.discard(pid)
        current = asyncio.current_task()
        if _generation_tasks.get(pid) is current:
            _generation_tasks.pop(pid, None)


async def _fail(pid: str, seq: int, emit, msg: str, *, script: str, plan: str,
                rounds: int = 0, fail_lines: str = "") -> None:
    # 失败也保留脚本，老师可在脚本 tab 基础上继续改
    store.finish_version(pid, seq, status="failed", script=script, plan=plan,
                         heal_rounds=rounds, error=msg, fail_lines=fail_lines)
    store.add_message(pid, "assistant", f"第 {seq} 版生成失败：{msg}", seq)
    await emit("version_failed", error=msg, script=script)


# ── 静态托管 ────────────────────────────────────────────────
config.ensure_dirs()   # mount 在 import 期执行，目录必须先就位
app.mount("/media", StaticFiles(directory=str(config.DATA_DIR)), name="media")
app.mount("/", StaticFiles(directory=str(config.FRONTEND_DIR), html=True), name="static")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("backend.main:app", host="0.0.0.0", port=config.PORT)
