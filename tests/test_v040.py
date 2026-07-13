from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from backend import db, generation_cache, lint, solid3d, store, style2d
from backend.llm import MockLLM, OpenAILLM
from backend.main import (MessageIn, _generating, _generation_tasks, _run_generation,
                          cancel_generation, post_message)


class Solid3DNormalizerTests(unittest.TestCase):
    def test_styles_are_added_without_overriding_explicit_key_line(self):
        source = """# perspective: 3d
A=(0,0,0)
B=(3,0,0)
edge=Segment(A,B)
SetLineThickness(edge,7)
face=Polygon(A,B,C)
SetFilling(face,0.8)
"""
        normalized = solid3d.normalize_script(source)
        self.assertIn("SetLineThickness(edge,7)", normalized)
        self.assertNotIn("SetLineThickness(edge,3)", normalized)
        self.assertIn("SetFilling(face,0.12)", normalized)
        self.assertIn("SetPointSize(A,5)", normalized)
        self.assertIn("ShowLabel(edge,false)", normalized)
        self.assertEqual(normalized, solid3d.normalize_script(normalized))

    def test_clear_prism_topology_gets_missing_edges(self):
        source = """# perspective: 3d
A=(0,0,0)
B=(3,0,0)
C=(0,2,0)
A1=(0,0,3)
B1=(3,0,3)
C1=(0,2,3)
base=Polygon(A,B,C)
top=Polygon(A1,B1,C1)
solid=Prism(base,Vector(A,A1))
"""
        normalized = solid3d.normalize_script(source)
        for edge in ("Segment(A,B)", "Segment(B,C)", "Segment(C,A)",
                     "Segment(A1,B1)", "Segment(B1,C1)", "Segment(C1,A1)",
                     "Segment(A,A1)", "Segment(B,B1)", "Segment(C,C1)"):
            self.assertIn(edge, normalized)
        _, issues = lint.preflight(normalized)
        self.assertEqual(issues, [])

    def test_duplicate_point_text_is_removed_but_explanation_is_kept(self):
        source = """# perspective: 3d
A=(0,0,0)
labelA=Text("A",A,true)
note=Text("顶点 A",(1,1,1))
"""
        normalized = solid3d.normalize_script(source)
        self.assertNotIn('labelA=Text("A",A,true)', normalized)
        self.assertIn('note=Text("顶点 A",(1,1,1))', normalized)


class Style2DNormalizerTests(unittest.TestCase):
    def test_visible_lines_get_explicit_dark_color_and_thickness(self):
        source = """# perspective: 2d
A=(0,0)
B=(4,0)
s=Segment(A,B)
c=Circle(A,2)
poly=Polygon(A,B,C)
"""
        normalized = style2d.normalize_script(source)
        for label in ("s", "c"):
            self.assertIn(f"SetColor({label},35,35,35)", normalized)
            self.assertIn(f"SetLineThickness({label},3)", normalized)
        self.assertNotIn("SetColor(poly,35,35,35)", normalized)

    def test_light_line_color_is_darkened_but_deep_accent_is_preserved(self):
        source = """# perspective: 2d
A=(0,0)
whiteLine=Segment(A,B)
SetColor(whiteLine,255,255,255)
accent=Circle(A,2)
SetColor(accent,170,40,40)
poly=Polygon(A,B,C)
SetColor(poly,245,245,245)
"""
        normalized = style2d.normalize_script(source)
        self.assertIn("SetColor(whiteLine,35,35,35)", normalized)
        self.assertIn("SetColor(poly,35,35,35)", normalized)
        self.assertIn("SetColor(accent,170,40,40)", normalized)

    def test_normalization_is_idempotent(self):
        source = "# perspective: 2d\nA=(0,0)\nc=Circle(A,2)\n"
        once = style2d.normalize_script(source)
        self.assertEqual(once, style2d.normalize_script(once))


class GenerationCacheTests(unittest.TestCase):
    def test_cache_key_covers_generation_choices_model_and_images(self):
        base = dict(prompt="画正方体", images=[], mode="figure", space="3d",
                    interactive=False, llm_name="gemini", llm_config={
                        "provider": "custom", "base_url": "https://example.com/v1",
                        "model": "gemini", "api_key": "secret-one",
                    })
        key = generation_cache.make_key(**base)
        with_new_secret = generation_cache.make_key(**{
            **base, "llm_config": {**base["llm_config"], "api_key": "secret-two"}
        })
        self.assertEqual(key, with_new_secret)
        self.assertNotEqual(key, generation_cache.make_key(**{**base, "interactive": True}))
        self.assertNotEqual(key, generation_cache.make_key(**{**base, "space": "2d"}))
        self.assertNotEqual(key, generation_cache.make_key(**{**base, "images": ["data:image/png;base64,AAAA"]}))
        self.assertNotEqual(key, generation_cache.make_key(**{**base, "llm_name": "演示模式"}))

    def test_only_verified_result_shape_is_persisted_and_hit_counted(self):
        with tempfile.TemporaryDirectory() as temp:
            db_path = Path(temp) / "cache.db"
            with patch("backend.config.DB_PATH", db_path), patch("backend.config.DATA_DIR", Path(temp)), \
                    patch("backend.config.PROJECTS_DIR", Path(temp) / "projects"):
                db.init_db()
                store.put_generation_cache("key", "plan", "A=(0,0)\n")
                first = store.get_generation_cache("key")
                self.assertEqual(first["plan"], "plan")
                self.assertEqual(first["hit_count"], 0)
                store.mark_generation_cache_used("key")
                self.assertEqual(store.get_generation_cache("key")["hit_count"], 1)


class GenerationCacheFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_second_new_project_reuses_verified_generation_without_llm_call(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            patches = (
                patch("backend.config.DB_PATH", root / "any2ggb.db"),
                patch("backend.config.DATA_DIR", root),
                patch("backend.config.PROJECTS_DIR", root / "projects"),
                patch("backend.main.config.active_flat", return_value={}),
                patch("backend.main._wait_verify", new=AsyncMock(return_value={
                    "ok": True, "failures": [], "objects": [],
                    "png_base64": "", "ggb_base64": "",
                })),
            )
            for item in patches:
                item.start()
            self.addCleanup(lambda: [item.stop() for item in reversed(patches)])
            db.init_db()

            first = store.create_project("first")["id"]
            store.add_message(first, "user", "画一个三角形")
            first_llm = MockLLM()
            with patch("backend.main.from_config", return_value=first_llm):
                await _run_generation(first, "画一个三角形", mode="figure", space="2d",
                                      interactive=False)
            self.assertEqual(store.list_versions(first)[0]["status"], "ok")

            second = store.create_project("second")["id"]
            store.add_message(second, "user", "画一个三角形")
            cached_llm = MockLLM()
            cached_llm.complete = Mock(side_effect=AssertionError("缓存命中时不应调用 LLM"))
            with patch("backend.main.from_config", return_value=cached_llm):
                await _run_generation(second, "画一个三角形", mode="figure", space="2d",
                                      interactive=False)
            self.assertEqual(store.list_versions(second)[0]["status"], "ok")
            with db.connect() as conn:
                hit_count = conn.execute("SELECT hit_count FROM generation_cache").fetchone()[0]
            self.assertEqual(hit_count, 1)


class InteractiveHtmlExportTests(unittest.TestCase):
    def test_frontend_contains_snapshot_based_html_export(self):
        source = (Path(__file__).parents[1] / "frontend" / "ggb_host.js").read_text(encoding="utf-8")
        self.assertIn("exportInteractiveHTML", source)
        self.assertIn("ggbBase64", source)
        self.assertIn("https://www.geogebra.org/apps/deployggb.js", source)


class CancellableLLMTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancelling_real_llm_request_closes_async_http_client(self):
        started = asyncio.Event()
        closed = asyncio.Event()

        class WaitingClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, _exc_type, _exc, _tb):
                closed.set()

            async def post(self, _url, **_kwargs):
                started.set()
                await asyncio.Future()

        llm = OpenAILLM("https://example.com/v1", "test-key", "test-model")
        with patch("backend.llm.httpx.AsyncClient", return_value=WaitingClient()):
            task = asyncio.create_task(llm.acomplete("system", "user"))
            await started.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task

        self.assertTrue(closed.is_set())


class CancelGenerationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        _generating.clear()
        _generation_tasks.clear()

    def tearDown(self):
        for task in list(_generation_tasks.values()):
            task.cancel()
        _generating.clear()
        _generation_tasks.clear()

    async def test_live_generation_can_be_cancelled_while_waiting_for_browser(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            reached_verify = asyncio.Event()

            async def wait_forever(_pid, _seq):
                reached_verify.set()
                await asyncio.Future()

            patches = (
                patch("backend.config.DB_PATH", root / "any2ggb.db"),
                patch("backend.config.DATA_DIR", root),
                patch("backend.config.PROJECTS_DIR", root / "projects"),
                patch("backend.main.config.active_flat", return_value={}),
                patch("backend.main.from_config", return_value=MockLLM()),
                patch("backend.main._wait_verify", new=wait_forever),
            )
            for item in patches:
                item.start()
            try:
                db.init_db()
                pid = store.create_project("cancel-live")["id"]
                await post_message(pid, MessageIn(text="画三角形"))
                await reached_verify.wait()
                result = await cancel_generation(pid)
                self.assertTrue(result["cancelled"])
                version = store.get_version(pid, 1)
                self.assertEqual(version["status"], "cancelled")
                self.assertEqual(version["error"], "用户手动停止")
                self.assertNotIn(pid, _generating)
                self.assertNotIn(pid, _generation_tasks)
            finally:
                for item in reversed(patches):
                    item.stop()

    async def test_stale_pending_version_can_be_cancelled_after_restart(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            patches = (
                patch("backend.config.DB_PATH", root / "any2ggb.db"),
                patch("backend.config.DATA_DIR", root),
                patch("backend.config.PROJECTS_DIR", root / "projects"),
            )
            for item in patches:
                item.start()
            try:
                db.init_db()
                pid = store.create_project("cancel-stale")["id"]
                store.create_version(pid, 1, "旧请求")
                result = await cancel_generation(pid)
                self.assertTrue(result["cancelled"])
                self.assertEqual(result["seqs"], [1])
                self.assertEqual(store.get_version(pid, 1)["status"], "cancelled")
                messages = store.list_messages(pid)
                self.assertEqual(messages[-1]["content"], "已手动停止第 1 版生成")
            finally:
                for item in reversed(patches):
                    item.stop()

    def test_frontend_exposes_stop_control_and_cancelled_timeline_state(self):
        root = Path(__file__).parents[1] / "frontend"
        html = (root / "index.html").read_text(encoding="utf-8")
        script = (root / "app.js").read_text(encoding="utf-8")
        self.assertIn('id="stopBtn"', html)
        self.assertIn("/cancel", script)
        self.assertIn('case "version_cancelled"', script)


if __name__ == "__main__":
    unittest.main()
