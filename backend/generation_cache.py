"""生成结果缓存键：只保存哈希，不保存题目或图片副本。"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any


CACHE_SCHEMA_VERSION = "any2ggb-generation-v1"


def _normalized_text(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _image_digest(image: str) -> str:
    payload = str(image or "")
    if payload.startswith("data:") and "," in payload:
        header, payload = payload.split(",", 1)
    else:
        header = "data:image/unknown;base64"
    digest = hashlib.sha256()
    digest.update(header.lower().encode("utf-8"))
    digest.update(b"\0")
    digest.update(payload.encode("ascii", errors="ignore"))
    return digest.hexdigest()


def make_key(*, prompt: str, images: list[str], mode: str, space: str,
             interactive: bool, llm_name: str, llm_config: dict[str, Any]) -> str:
    """同一输入、生成选项、模型和提示词版本才会命中。API Key 永不入键。"""
    material = {
        "schema": CACHE_SCHEMA_VERSION,
        "prompt": _normalized_text(prompt),
        "images": [_image_digest(image) for image in images],
        "mode": mode,
        "space": space,
        "interactive": bool(interactive),
        "provider": str(llm_config.get("provider", "")),
        "base_url": str(llm_config.get("base_url", "")).rstrip("/"),
        "model": str(llm_config.get("model", "")) or str(llm_name),
        "runtime": str(llm_name),  # 防止同一预设从演示模式切到真实模型后误命中
    }
    raw = json.dumps(material, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
