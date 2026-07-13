from __future__ import annotations

import unittest
from unittest.mock import patch

from fastapi import HTTPException

from backend import edits, engine, modes
from backend.llm import MockLLM
from backend.main import ManualVersionIn, MessageIn, save_manual_version


class ModePromptTests(unittest.TestCase):
    def test_every_mode_has_distinct_professional_prompt(self):
        prompts = {key: modes.prompt_for(key) for key in modes.MODES}
        self.assertEqual(len(prompts), 4)
        self.assertEqual(len(set(prompts.values())), 4)
        for key, prompt in prompts.items():
            self.assertIn(modes.MODES[key]["title"], prompt)

    def test_space_rules_are_explicit(self):
        self.assertIn("# perspective: 2d", engine.sys_scriptgen("figure", "2d"))
        three_d = engine.sys_scriptgen("solve", "3d")
        self.assertIn("# perspective: 3d", three_d)
        self.assertIn("# view3d:", three_d)
        self.assertIn("三维坐标", three_d)

    def test_space_directive_is_forced_and_replaced(self):
        src = "# step_01：主体\nA=(0,0)\n"
        self.assertTrue(engine.ensure_space_directive(src, "2d").startswith("# perspective: 2d\n"))
        switched = engine.ensure_space_directive("# perspective: 2d\nA=(0,0,0)", "3d")
        self.assertTrue(switched.startswith("# perspective: 3d\n"))
        self.assertNotIn("perspective: 2d", switched)

    def test_interaction_prompt_is_controlled_by_checkbox(self):
        static_plan = engine.sys_plan("figure", "2d", False)
        interactive_plan = engine.sys_plan("figure", "2d", True)
        self.assertIn("【静态模式】", static_plan)
        self.assertIn("interaction 必须为空", static_plan)
        self.assertIn("【互动模式】", interactive_plan)
        self.assertIn("取值范围/初值", interactive_plan)

    def test_interaction_preflight_enforces_both_directions(self):
        static_script = "# perspective: 2d\nA=(0,0)\nB=(3,0)\ns=Segment(A,B)\n"
        interactive_script = static_script + "a=Slider(1,5,0.5)\n"
        self.assertEqual(engine.interaction_issues(static_script, False), [])
        self.assertEqual(engine.interaction_issues(interactive_script, True), [])
        self.assertTrue(engine.interaction_issues(interactive_script, False))
        self.assertTrue(engine.interaction_issues(static_script, True))
        self.assertTrue(engine.interaction_issues(static_script + "StartAnimation(a)\n", True))
        self.assertFalse(engine.script_is_interactive(static_script))
        self.assertTrue(engine.script_is_interactive(interactive_script))

    def test_message_defaults_to_static(self):
        self.assertFalse(MessageIn(text="画三角形").interactive)
        self.assertTrue(MessageIn(text="做参数探究", interactive=True).interactive)

    def test_demo_model_respects_interaction_mode(self):
        llm = MockLLM()
        static = llm.complete(engine.sys_scriptgen("figure", "2d", False),
                              "画抛物线", task="scriptgen")
        interactive = llm.complete(engine.sys_scriptgen("figure", "2d", True),
                                   "画抛物线", task="scriptgen")
        self.assertFalse(engine.script_is_interactive(static))
        self.assertTrue(engine.script_is_interactive(interactive))

    def test_static_plan_text_is_normalized(self):
        plan = {
            "brief": {"topic": "抛物线"},
            "steps": [
                {"id": "step_01", "teaches": "创建滑杆",
                 "shows": "滑杆 a，取值范围 -5 到 5，步长 0.1，初始值为 1"},
                {"id": "step_02", "teaches": "绘制动态抛物线",
                 "shows": "拖动滑杆观察变化"},
            ],
            "interaction": "滑杆控制开口",
        }
        static = engine.normalize_plan_interaction(plan, False)
        rendered = engine.format_plan(static)
        self.assertNotIn("滑杆", rendered)
        self.assertNotIn("动态", rendered)
        self.assertNotIn("交互：", rendered)
        self.assertIn("固定参数 a=1", rendered)


class DirectedEditTests(unittest.TestCase):
    def test_search_replace_changes_only_target(self):
        original = "# perspective: 2d\n# step_01：主体\nA=(0,0)\nB=(4,0)\ns=Segment(A,B)\n"
        raw = """<<<<<<< SEARCH
B=(4,0)
=======
B=(6,0)
>>>>>>> REPLACE"""
        result = edits.apply_blocks(original, edits.parse_blocks(raw))
        self.assertEqual(result.applied, 1)
        self.assertIn("B=(6,0)", result.code)
        self.assertIn("A=(0,0)", result.code)
        self.assertIn("s=Segment(A,B)", result.code)
        self.assertEqual(len(result.code.splitlines()), len(original.splitlines()))


class ManualVersionTests(unittest.TestCase):
    @patch("backend.main._save_media", return_value=("projects/p/v2.png", "projects/p/v2.ggb"))
    @patch("backend.main.store.add_message")
    @patch("backend.main.store.finish_version")
    @patch("backend.main.store.create_version")
    @patch("backend.main.store.next_seq", return_value=2)
    @patch("backend.main.store.get_project", return_value={"id": "p", "archived": 0})
    def test_manual_script_becomes_ok_version(self, _get, _next, create, finish, add, _media):
        body = ManualVersionIn(script="# perspective: 2d\nA=(0,0)\n", plan="主体")
        result = save_manual_version("p", body)
        self.assertEqual(result["seq"], 2)
        create.assert_called_once_with("p", 2, "手动编辑脚本")
        self.assertEqual(finish.call_args.kwargs["status"], "ok")
        add.assert_called_once_with("p", "system", "已保存手动修改为第 2 版", 2)

    @patch("backend.main.store.get_project", return_value={"id": "p", "archived": 0})
    def test_manual_script_rejects_preflight_error(self, _get):
        with self.assertRaises(HTTPException) as caught:
            save_manual_version("p", ManualVersionIn(script="A=ImaginaryCommand(1)"))
        self.assertEqual(caught.exception.status_code, 400)


if __name__ == "__main__":
    unittest.main()
