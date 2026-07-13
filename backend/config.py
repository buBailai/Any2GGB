"""全局配置与路径约定（仿 Any2Manim：SQLite + 文件落盘、数据自主）。

Any2GGB 没有服务器端渲染——GeoGebra 引擎跑在浏览器里，
后端只负责 LLM 编排、项目存储、素材与在线升级。
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

# ── 目录约定 ──────────────────────────────────────────────
APP_DIR = Path(__file__).resolve().parent.parent          # .../Any2GGB/app
BACKEND_DIR = APP_DIR / "backend"
FRONTEND_DIR = APP_DIR / "frontend"
PROMPTS_DIR = BACKEND_DIR / "prompts"

# data 可以是指向 data.nosync 的符号链接（防 iCloud 驱逐）
DATA_DIR = APP_DIR / "data"
PROJECTS_DIR = DATA_DIR / "projects"
DB_PATH = DATA_DIR / "any2ggb.db"
CONFIG_PATH = DATA_DIR / "config.json"     # API 厂商/Key/模型（BYO-Key，本地存）

PORT = int(os.environ.get("A2G_PORT", "8868"))


# ── 在线更新：免安装包探测 / 版本真值 / 更新源（对齐 Any2Manim 机制）──────
def _detect_root() -> "Path | None":
    """判定当前是不是免安装包运行；是则返回包根，否则 None（dev/pip 安装）。"""
    env = os.environ.get("A2G_ROOT", "").strip()
    if env:
        p = Path(env)
        return p if p.is_dir() else None
    root = APP_DIR.parent
    if (root / "python").is_dir():
        return root
    return None


PACKAGE_ROOT = _detect_root()
PORTABLE = PACKAGE_ROOT is not None

# 更新源：开源脱敏——源码默认留空，官方免安装包靠启动器注入 A2G_UPDATE_URL，
# 用户也可在页面「检查更新」里手填（存 settings 表，随 data 保留、升级不丢）。
DEFAULT_UPDATE_URL = os.environ.get("A2G_UPDATE_URL", "").strip().rstrip("/")

_VER_RE = re.compile(r"^##\s*\[(\d+\.\d+\.\d+)\]")


def app_version() -> str:
    """当前版本真值 = CHANGELOG.md 顶部首个 `## [X.Y.Z]`（发版只改文档）。"""
    try:
        for ln in (APP_DIR / "CHANGELOG.md").read_text(encoding="utf-8").splitlines():
            m = _VER_RE.match(ln.strip())
            if m:
                return m.group(1)
    except OSError:
        pass
    return "0.0.0"


APP_VERSION = app_version()

# ── 自愈循环预算（前端执行 + 后端修，回合数为主约束）────────────
HEAL_MAX_ROUNDS = 3          # 生成后最多修 N 轮（每轮 = 1 次 LLM + 1 次前端执行）
VERIFY_TIMEOUT = 120         # 等前端回报执行结果的超时(s)——超时视为放弃本轮


def ensure_dirs() -> None:
    for d in (DATA_DIR, PROJECTS_DIR):
        d.mkdir(parents=True, exist_ok=True)


def project_dir(pid: str) -> Path:
    return PROJECTS_DIR / pid


# ── API 配置：按厂商分别长期保存（对齐 Any2Manim 成熟实现）─────────
def read_config() -> dict:
    """读 config.json，统一成 {active, providers:{p:{base_url,api_key,model}}}。"""
    if not CONFIG_PATH.exists():
        return {"active": "", "providers": {}}
    try:
        raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {"active": "", "providers": {}}
    if isinstance(raw.get("providers"), dict):
        raw.setdefault("active", "")
        return raw
    prov = raw.get("provider", "")
    out: dict = {"active": prov, "providers": {}}
    if prov:
        out["providers"][prov] = {"base_url": raw.get("base_url", ""),
                                  "api_key": raw.get("api_key", ""),
                                  "model": raw.get("model", "")}
    return out


def provider_flat(p: str) -> dict:
    d = read_config().get("providers", {}).get(p, {})
    return {"provider": p, "base_url": d.get("base_url", ""),
            "api_key": d.get("api_key", ""), "model": d.get("model", "")}


def active_flat() -> dict:
    return provider_flat(read_config().get("active", ""))


def save_provider(provider: str, base_url: str, api_key: str, model: str) -> None:
    """保存某厂商配置并设为激活。api_key 留空时保留该厂商原 Key。"""
    c = read_config()
    provs = c.setdefault("providers", {})
    cur = provs.get(provider, {})
    if not api_key and cur.get("api_key"):
        api_key = cur["api_key"]
    provs[provider] = {"base_url": base_url, "api_key": api_key, "model": model}
    c["active"] = provider
    ensure_dirs()
    CONFIG_PATH.write_text(json.dumps(c, ensure_ascii=False, indent=2), encoding="utf-8")
