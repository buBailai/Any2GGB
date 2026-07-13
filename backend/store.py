"""数据访问层：projects / versions / messages / settings。"""
from __future__ import annotations

import time
import uuid
from typing import Optional

from . import config, db


def _id() -> str:
    return uuid.uuid4().hex[:12]


# ── projects ────────────────────────────────────────────────
def create_project(title: str, subject: str = "") -> dict:
    now = time.time()
    pid = "proj_" + _id()
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO projects (id,title,subject,archived,created_at,updated_at)"
            " VALUES (?,?,?,0,?,?)", (pid, title, subject, now, now))
    config.project_dir(pid).mkdir(parents=True, exist_ok=True)
    return {"id": pid, "title": title, "subject": subject}


def list_projects(archived: bool = False) -> list[dict]:
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT * FROM projects WHERE archived=? ORDER BY updated_at DESC",
            (1 if archived else 0,)).fetchall()
    return db.rows_to_dicts(rows)


def get_project(pid: str) -> Optional[dict]:
    with db.connect() as conn:
        r = conn.execute("SELECT * FROM projects WHERE id=?", (pid,)).fetchone()
    return dict(r) if r else None


def touch_project(pid: str) -> None:
    with db.connect() as conn:
        conn.execute("UPDATE projects SET updated_at=? WHERE id=?", (time.time(), pid))


def rename_project(pid: str, title: str) -> None:
    with db.connect() as conn:
        conn.execute("UPDATE projects SET title=?, updated_at=? WHERE id=?",
                     (title, time.time(), pid))


def set_archived(pid: str, archived: bool) -> None:
    with db.connect() as conn:
        conn.execute("UPDATE projects SET archived=?, updated_at=? WHERE id=?",
                     (1 if archived else 0, time.time(), pid))


def delete_project(pid: str) -> None:
    with db.connect() as conn:
        conn.execute("DELETE FROM versions WHERE project_id=?", (pid,))
        conn.execute("DELETE FROM messages WHERE project_id=?", (pid,))
        conn.execute("DELETE FROM projects WHERE id=?", (pid,))


# ── versions ────────────────────────────────────────────────
def next_seq(pid: str) -> int:
    with db.connect() as conn:
        r = conn.execute("SELECT MAX(seq) AS m FROM versions WHERE project_id=?",
                         (pid,)).fetchone()
    return (r["m"] or 0) + 1


def create_version(pid: str, seq: int, prompt: str) -> str:
    vid = "ver_" + _id()
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO versions (id,project_id,seq,prompt,status,created_at)"
            " VALUES (?,?,?,?,'pending',?)", (vid, pid, seq, prompt, time.time()))
    return vid


def finish_version(pid: str, seq: int, *, status: str, script: str = "",
                   plan: str = "", heal_rounds: int = 0, error: str = "",
                   fail_lines: str = "", thumb_path: str = "",
                   ggb_path: str = "") -> None:
    with db.connect() as conn:
        conn.execute(
            "UPDATE versions SET status=?, script=?, plan=?, heal_rounds=?,"
            " error=?, fail_lines=?, thumb_path=?, ggb_path=?"
            " WHERE project_id=? AND seq=?",
            (status, script, plan, heal_rounds, error, fail_lines,
             thumb_path, ggb_path, pid, seq))
        if status == "ok":
            conn.execute("UPDATE projects SET current_version=?, updated_at=? WHERE id=?",
                         (seq, time.time(), pid))


def set_version_media(pid: str, seq: int, *, thumb_path: str = "",
                      ggb_path: str = "") -> None:
    with db.connect() as conn:
        if thumb_path:
            conn.execute("UPDATE versions SET thumb_path=? WHERE project_id=? AND seq=?",
                         (thumb_path, pid, seq))
        if ggb_path:
            conn.execute("UPDATE versions SET ggb_path=? WHERE project_id=? AND seq=?",
                         (ggb_path, pid, seq))


def get_version(pid: str, seq: int) -> Optional[dict]:
    with db.connect() as conn:
        r = conn.execute("SELECT * FROM versions WHERE project_id=? AND seq=?",
                         (pid, seq)).fetchone()
    return dict(r) if r else None


def list_versions(pid: str) -> list[dict]:
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT seq,prompt,status,thumb_path,heal_rounds,error,created_at"
            " FROM versions WHERE project_id=? ORDER BY seq", (pid,)).fetchall()
    return db.rows_to_dicts(rows)


def cancel_pending_versions(pid: str, seq: Optional[int] = None,
                            reason: str = "用户手动停止") -> list[int]:
    """把仍为 pending 的版本原子标记为 cancelled，返回实际取消的序号。"""
    with db.connect() as conn:
        if seq is None:
            rows = conn.execute(
                "SELECT seq FROM versions WHERE project_id=? AND status='pending' ORDER BY seq",
                (pid,),
            ).fetchall()
            seqs = [int(row["seq"]) for row in rows]
            if seqs:
                conn.execute(
                    "UPDATE versions SET status='cancelled',error=?"
                    " WHERE project_id=? AND status='pending'",
                    (reason, pid),
                )
        else:
            row = conn.execute(
                "SELECT seq FROM versions WHERE project_id=? AND seq=? AND status='pending'",
                (pid, seq),
            ).fetchone()
            seqs = [int(row["seq"])] if row else []
            if seqs:
                conn.execute(
                    "UPDATE versions SET status='cancelled',error=?"
                    " WHERE project_id=? AND seq=? AND status='pending'",
                    (reason, pid, seq),
                )
    return seqs


def latest_script(pid: str) -> Optional[str]:
    """最近一个有脚本的版本（含失败版——失败后老师可在这次尝试基础上继续改）。"""
    with db.connect() as conn:
        r = conn.execute(
            "SELECT script FROM versions WHERE project_id=? AND script IS NOT NULL"
            " AND script != '' ORDER BY seq DESC LIMIT 1", (pid,)).fetchone()
    return r["script"] if r else None


def set_current(pid: str, seq: int) -> None:
    with db.connect() as conn:
        conn.execute("UPDATE projects SET current_version=?, updated_at=? WHERE id=?",
                     (seq, time.time(), pid))


def first_user_prompt(pid: str) -> str:
    with db.connect() as conn:
        r = conn.execute(
            "SELECT content FROM messages WHERE project_id=? AND role='user'"
            " ORDER BY created_at LIMIT 1", (pid,)).fetchone()
    return r["content"] if r else ""


# ── messages ────────────────────────────────────────────────
def add_message(pid: str, role: str, content: str,
                version_seq: Optional[int] = None) -> None:
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO messages (id,project_id,role,content,version_seq,created_at)"
            " VALUES (?,?,?,?,?,?)",
            ("msg_" + _id(), pid, role, content, version_seq, time.time()))


def list_messages(pid: str) -> list[dict]:
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT role,content,version_seq,created_at FROM messages"
            " WHERE project_id=? ORDER BY created_at", (pid,)).fetchall()
    return db.rows_to_dicts(rows)


# ── settings（在线升级等持久 KV）─────────────────────────────
def get_setting(key: str, default: str = "") -> str:
    with db.connect() as conn:
        r = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return r["value"] if r and r["value"] is not None else default


def set_setting(key: str, value: str) -> None:
    with db.connect() as conn:
        conn.execute("INSERT INTO settings (key,value) VALUES (?,?)"
                     " ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))


# ── generation cache（只存成功且经浏览器验证的计划/脚本）──────
def get_generation_cache(cache_key: str) -> Optional[dict]:
    with db.connect() as conn:
        row = conn.execute(
            "SELECT plan,script,created_at,last_used_at,hit_count"
            " FROM generation_cache WHERE cache_key=?", (cache_key,)
        ).fetchone()
    return dict(row) if row else None


def put_generation_cache(cache_key: str, plan: str, script: str) -> None:
    now = time.time()
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO generation_cache(cache_key,plan,script,created_at,last_used_at,hit_count)"
            " VALUES (?,?,?,?,?,0)"
            " ON CONFLICT(cache_key) DO UPDATE SET plan=excluded.plan,script=excluded.script,"
            " last_used_at=excluded.last_used_at",
            (cache_key, plan, script, now, now),
        )


def mark_generation_cache_used(cache_key: str) -> None:
    with db.connect() as conn:
        conn.execute(
            "UPDATE generation_cache SET hit_count=hit_count+1,last_used_at=? WHERE cache_key=?",
            (time.time(), cache_key),
        )
