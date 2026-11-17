"""Terminal boundaries that differ from the machine JSON interface."""
import io
import os
import unittest
from unittest.mock import patch

from open_agent_world.cli.render import Renderer, clean, display_width, short_ids


class Terminal(io.StringIO):
    def isatty(self):
        return True


class RendererTests(unittest.TestCase):
    def setUp(self):
        terminal = patch.dict(os.environ, {"TERM": "xterm-256color"})
        terminal.start()
        self.addCleanup(terminal.stop)

    def test_redirected_cards_keep_ids_without_terminal_codes(self):
        output = io.StringIO()
        Renderer(output, color=True).cards([
            {"id": "a-long-complete-id-123456789", "type": "core.agent",
             "name": "Research\x1b[2J\x1b]0;forged title\x07\u202eAgent", "status": "idle"},
        ])
        self.assertEqual(output.getvalue(), "a-long-complete-id-123456789  ResearchAgent  core.agent  idle\n")
        output = Terminal()
        Renderer(output, width=88, color=False).cards([
            {"id": "safe-id\x1b[2Jpayload", "type": "agent", "name": "Helper", "status": "idle"},
        ])
        self.assertNotIn("\x1b", output.getvalue())

    def test_compact_cards_fit_narrow_terminal_and_keep_usable_ids(self):
        for width in (8, 24, 67, 88):
            with self.subTest(width=width):
                output = Terminal()
                Renderer(output, width=width, color=False).cards([
                    {"id": "agent-123456789", "type": "core.agent", "name": "晶体结构分析助手", "status": "idle"},
                    {"id": "notes-123456789", "type": "core.text", "name": "Notes", "status": "ready"},
                    {"id": "tools-123456789", "type": "core.sandbox", "name": "Python", "status": "stopped"},
                ])
                self.assertIn("▣", output.getvalue())
                self.assertNotIn("╭", output.getvalue())
                self.assertTrue(all(display_width(line) <= width for line in output.getvalue().splitlines()))
                self.assertIn("agent-12", output.getvalue())
                if width >= 67:
                    self.assertEqual(len(output.getvalue().splitlines()), 4)
                    self.assertIn("晶体结构分析助手", output.getvalue())

    def test_shared_id_prefixes_expand_instead_of_being_truncated(self):
        cards = [{"id": "12345678a-rest", "name": "One", "type": "text", "status": "ready"},
                 {"id": "12345678b-rest", "name": "Two", "type": "text", "status": "ready"}]
        self.assertEqual(short_ids(cards), {"12345678a-rest": "12345678a", "12345678b-rest": "12345678b"})
        output = Terminal()
        Renderer(output, width=88, color=False).cards(cards)
        self.assertIn("12345678a\n", output.getvalue())
        self.assertIn("12345678b\n", output.getvalue())
        self.assertNotIn("…", output.getvalue())

    def test_single_card_makes_name_explicit_without_a_full_width_frame(self):
        output = Terminal()
        Renderer(output, width=140, color=False).card({"id": "full-card-identity", "short_id": "full-car",
            "name": "New Agent", "type": "agent", "status": "idle"})
        text = output.getvalue()
        self.assertIn("Name  New Agent", text)
        self.assertIn("ID  full-car", text)
        self.assertNotIn("full-card-identity", text)
        self.assertTrue(all(display_width(line) <= 72 for line in text.splitlines()))

    def test_no_color_and_plain_modes(self):
        with patch.dict(os.environ, {"NO_COLOR": ""}):
            output = Terminal()
            Renderer(output, color=True).card({"id": "one", "name": "Notes", "type": "core.text"})
            self.assertIn("╭", output.getvalue())
            self.assertNotIn("\x1b", output.getvalue())
        output = Terminal()
        Renderer(output, plain=True, color=True).cards([{"id": "one", "name": "Notes", "type": "core.text"}])
        self.assertEqual(output.getvalue(), "one  Notes  core.text\n")

    def test_result_preserves_paragraphs_without_terminal_instructions(self):
        output = io.StringIO()
        Renderer(output).result({"run": {"run_id": "run-1", "status": "succeeded"},
                                 "output_text": "First line\n\nSecond \x1b[31mline\x1b[0m"})
        self.assertEqual(output.getvalue(), "run-1  succeeded\n\nFirst line\n\nSecond line\n")
        self.assertEqual(clean("safe\x9b\u202e\x00"), "safe")

    def test_doctor_explains_disconnection_and_next_step(self):
        output = io.StringIO()
        Renderer(output).status({"connected": False, "cli_version": "0.7.1", "python": "3.12.8",
                                 "hint": "Start OAW, then run oaw doctor."})
        self.assertEqual(output.getvalue(), "OAW\nNot connected.\nCLI 0.7.1\n\nStart OAW, then run oaw doctor.\n")

    def test_schema_explains_fields_and_relationships_without_json(self):
        output = io.StringIO()
        Renderer(output).schema({"id": "example.agent", "label": "Agent", "default_config": {"secret": "unused"},
            "config_schema": {"properties": {
                "prompt": {"type": "string", "description": "What this agent should do."},
                "mode": {"anyOf": [{"$ref": "#/$defs/Mode"}, {"type": "null"}]}},
                "$defs": {"Mode": {"type": "string", "enum": ["auto", "manual"]}}, "required": ["prompt"]},
            "relationships": [{"id": "read", "label": "Read notes"}]})
        text = output.getvalue()
        self.assertIn("prompt  string  required\n    What this agent should do.", text)
        self.assertIn("mode  string | null", text)
        self.assertIn("read  Read notes", text)
        self.assertNotIn("$defs", text)
        self.assertNotIn("unused", text)

    def test_submission_and_named_connections_are_actionable(self):
        output = io.StringIO()
        renderer = Renderer(output)
        renderer.submitted({"run_id": "run-123", "agent_name": "Researcher"})
        renderer.connections([{"source": "one", "target": "two", "source_name": "Researcher",
                               "target_name": "Notes", "relationship": "read"}], [])
        self.assertEqual(output.getvalue(), "Started Researcher\nrun-123\n\noaw wait run-123\nResearcher  -- read ->  Notes\n")

    def test_type_overview_groups_plugins_without_dumping_every_type(self):
        catalog = {"node_types": [
            {"id": "agent", "label": "Agent", "deck_id": "agents", "deck_label": "Agents"},
            {"id": "text", "label": "Text file", "deck_id": "objects", "deck_label": "Objects"},
            {"id": "tools.time.clock", "label": "Clock", "deck_id": "time", "deck_label": "Time"},
            {"id": "tools.time.counter", "label": "Counter", "deck_id": "time", "deck_label": "Time"}]}
        output = io.StringIO()
        Renderer(output).type_groups(catalog)
        text = output.getvalue()
        self.assertTrue(text.startswith("Basics\n"))
        self.assertIn("Text file", text)
        self.assertIn("time  Time  (2)", text)
        self.assertIn("oaw types GROUP", text)
        self.assertNotIn("tools.time.clock", text)
        output = io.StringIO()
        Renderer(output).types({"node_types": catalog["node_types"][2:]})
        self.assertTrue(output.getvalue().startswith("Time\n"))
        self.assertIn("tools.time.clock", output.getvalue())

    def test_connection_candidates_and_failed_runs_read_as_actions(self):
        output = io.StringIO()
        renderer = Renderer(output)
        renderer.connected({"id": "edge-1", "source_name": "Helper", "target_name": "Python",
                            "relationship": "execute_manage"})
        renderer.candidates([{"id": "execute", "name": "Execute"},
                             {"id": "execute_manage", "name": "Execute + Start/Stop"}])
        renderer.runs([{"run_id": "run-1", "agent_name": "Helper", "status": "failed",
                        "error": "No default model."}])
        text = output.getvalue()
        self.assertIn("Connected\nedge-1  Helper  -- execute_manage ->  Python", text)
        self.assertIn("1  execute\n", text)
        self.assertIn("2  execute_manage  Execute + Start/Stop", text)
        self.assertIn("run-1  Helper  failed\n  No default model.", text)
        self.assertNotIn('"source"', text)

    def test_chat_shows_speakers_and_text_without_session_metadata(self):
        output = io.StringIO()
        Renderer(output).chat({"card_name": "Research", "session_id": "hidden-session", "conversation_id": "hidden-conversation",
            "replies": [{"agent_name": "Helper", "text": "Hello\n\nThere", "status": "succeeded"},
                        {"agent_name": "Reviewer", "status": "failed", "error": "Model unavailable."}]})
        self.assertEqual(output.getvalue(), "Helper\nHello\n\nThere\n\nReviewer\nError: Model unavailable.\n")

    def test_models_show_the_default_without_credentials_or_urls(self):
        output = io.StringIO()
        renderer = Renderer(output)
        renderer.models({"default_model": "oaw:model:main", "connections": [{"name": "Local service", "api_key": "hidden-secret",
            "base_url": "https://hidden.example", "auth_mode": "none", "models": [{"id": "main", "name": "Research model"}]}]})
        self.assertEqual(output.getvalue(), "Research model  Local service  (default)\n")
        output = io.StringIO()
        Renderer(output).models({"connections": [], "default_model": None})
        self.assertIn("No default model. Open OAW Settings / Models", output.getvalue())


if __name__ == "__main__":
    unittest.main()
