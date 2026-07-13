"""SQLite 元数据层（仿 Any2Manim）。

核心原则：脚本廉价、快照只是加速 —— 长期存「GGB 脚本 + 元数据 + 缩略图」，
版本回溯靠脚本在前端重放。
"""
from __future__ import annotations

import sqlite3
from typing import Any

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id          TEXT PRIMARY KEY,
    title       TEXT NOT NULL,
    subject     TEXT,
    current_version INTEGER,
    archived    INTEGER NOT NULL DEFAULT 0,
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS versions (
    id          TEXT PRIMARY KEY,
    project_id  TEXT NOT NULL,
    seq         INTEGER NOT NULL,          -- 项目内自增版本号 v1,v2...
    prompt      TEXT,                      -- 触发这一版的用户指令
    plan        TEXT,                      -- 教学步骤计划文本
    script      TEXT,                      -- GGB 命令脚本（权威产物）
    ggb_path    TEXT,                      -- .ggb 快照(相对 data/，前端回传)
    thumb_path  TEXT,                      -- PNG 缩略图(相对 data/)
    status      TEXT NOT NULL,             -- pending|ok|failed|cancelled
    heal_rounds INTEGER DEFAULT 0,
    error       TEXT,                      -- 失败时的大白话原因
    fail_lines  TEXT,                      -- 失败行摘要（遥测：挖数据补避坑清单）
    created_at  REAL NOT NULL,
    UNIQUE(project_id, seq)
);

CREATE TABLE IF NOT EXISTS messages (
    id          TEXT PRIMARY KEY,
    project_id  TEXT NOT NULL,
    role        TEXT NOT NULL,             -- user|assistant|system
    content     TEXT NOT NULL,
    version_seq INTEGER,
    created_at  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS generation_cache (
    cache_key     TEXT PRIMARY KEY,
    plan          TEXT NOT NULL,
    script        TEXT NOT NULL,
    created_at    REAL NOT NULL,
    last_used_at  REAL NOT NULL,
    hit_count     INTEGER NOT NULL DEFAULT 0
);
"""


def connect() -> sqlite3.Connection:
    config.ensure_dirs()
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)


def rows_to_dicts(rows: Any) -> list[dict]:
    return [dict(r) for r in rows]
