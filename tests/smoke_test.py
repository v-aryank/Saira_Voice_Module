"""Offline smoke tests: routing, wake word, helpers, discovery, state machine.

Run from the project root:   python -m unittest tests.smoke_test -v
No microphone, speaker or internet connection is needed.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config as cfg
from commands import register_all
from commands.app_discovery import DesktopAppIndex, parse_desktop_file
from commands.applications import ApplicationRegistry, clean_target
from commands.development import copy_template
from commands.shell import RestrictedShell
from core.command_router import Action, CommandContext, CommandRouter
from core.state import State, StateMachine
from core.wake_word import match_wake_phrase
from audio.speaker import split_for_speech
from utils.helpers import normalize, slugify, strip_command_fillers
from utils.logger import SyraLogger


def make_router(): 
    log = SyraLogger()
    spoken: list[str] = []
    router = CommandRouter(log)
    register_all(router, log)
    ctx = CommandContext(say=spoken.append, ask=lambda prompt, timeout=0: None, log=log)
    return router, ctx, spoken


class TextTests(unittest.TestCase):
    def test_normalize(self):
        self.assertEqual(normalize("What's today's date?"), "whats todays date")

    def test_fillers(self):
        self.assertEqual(strip_command_fillers("Syra, could you open Chrome please"), "open chrome")

    def test_slug(self):
        self.assertEqual(slugify("My Portfolio Site!"), "my-portfolio-site")

    def test_clean_target(self):
        self.assertEqual(clean_target("the Visual Studio Code app"), "visual studio code")


class WakeWordTests(unittest.TestCase):
    def test_matches(self):
        for phrase in ("Wake up Syra", "wake up sarah", "Wake up, Sierra!", "wakeup syra", "hey wake up syra",
                       "Wake up Sira", "Wake Up Sid", "WAKE UP!!", "  wake   up   syra  ", "wake-up syra",
                       "wake up", "Wake up.", "wake up syria"):
            self.assertIsNotNone(match_wake_phrase(phrase), phrase)

    def test_remainder(self):
        self.assertEqual(match_wake_phrase("wake up syra open chrome").remainder, "open chrome")
        self.assertEqual(match_wake_phrase("wake up").remainder, "")

    def test_rejects(self):
        for phrase in ("open chrome", "what time is it", "wake up at seven", "syra", "wake", "Wake!",
                       "I said wake up the neighbours", "the alarm will wake up", "wake me up", ""):
            self.assertIsNone(match_wake_phrase(phrase), phrase)

    def test_debug_line(self):
        lines = []
        class Log:
            def debug(self, message): lines.append(message)
        match_wake_phrase("Wake Up, Sid!", Log())
        match_wake_phrase("wake", Log())
        self.assertIn("'wake up sid' -> MATCH", lines[0])
        self.assertIn("'wake' -> no match", lines[1])


class RoutingTests(unittest.TestCase):
    def setUp(self):
        self.router, self.ctx, self.spoken = make_router()

    def route(self, text):
        return self.router.dispatch(text, self.ctx)

    def test_sleep_variants(self):
        for phrase in ("go to sleep", "Sleep Syra", "quiet", "quite", "quit", "Stop listening", "terminal closed"):
            self.assertEqual(self.route(phrase).action, Action.SLEEP, phrase)

    def test_shutdown_requires_name(self):
        self.assertEqual(self.route("exit syra").action, Action.SHUTDOWN)
        self.assertNotEqual(self.route("exit").action, Action.SHUTDOWN)

    def test_time_and_date(self):
        self.assertTrue(self.route("What time is it?").reply.startswith("It's"))
        self.assertTrue(self.route("What is today's date?").reply.startswith("Today is"))

    def test_open_known_website(self):
        import commands.web as web
        opened = []
        web.open_url = lambda url: opened.append(url) or True  # type: ignore[assignment]
        self.route("Open YouTube")
        self.assertEqual(opened, ["https://www.youtube.com"])
        self.assertEqual(self.spoken, ["Opening YouTube."])

    def test_open_unknown(self):
        reply = self.route("open zzqxv nonexistent thing").reply
        self.assertIn("couldn't find", reply)

    def test_wikipedia_route_is_selected(self):
        import commands.wiki as wiki
        wiki._fetch = lambda q, n: wiki.WikiAnswer("Nikola Tesla", "Tesla was an inventor.", False)  # type: ignore
        self.assertEqual(self.route("Who is Nikola Tesla?").reply, "Tesla was an inventor.")
        self.assertEqual(self.route("Tell me about Alan Turing").reply, "Tesla was an inventor.")

    def test_who_are_you_is_not_wikipedia(self):
        self.assertIn("SYRA", self.route("who are you").reply)

    def test_unknown_command(self):
        self.assertFalse(self.route("frobnicate the widgets").handled)

    def test_webpage_architecture_route(self):
        import commands.development as dev
        called = []
        dev.create_webpage_architecture = lambda ctx, log: called.append(1) or "ok"  # type: ignore
        router, ctx, _ = make_router()
        self.assertEqual(router.dispatch("Create a webpage architecture", ctx).reply, "ok")
        self.assertEqual(router.dispatch("make a web page architecture", ctx).reply, "ok")


class RegistryTests(unittest.TestCase):
    def test_aliases(self):
        registry = ApplicationRegistry(cfg.APPLICATIONS)
        for alias in ("visual studio code", "vs code", "vscode", "VS Code"):
            self.assertEqual(registry.resolve(alias).key, "visual studio code", alias)
        self.assertEqual(registry.resolve("google chrome").display, "Chrome")


class DiscoveryTests(unittest.TestCase):
    def test_parse_and_match(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "org.gnome.Calculator.desktop"
            path.write_text("[Desktop Entry]\nType=Application\nName=Calculator\nName[bn]=x\n"
                            "Exec=gnome-calculator\nKeywords=math;calc;\n[Desktop Action a]\nName=Nope\n")
            hidden = Path(tmp) / "hidden.desktop"
            hidden.write_text("[Desktop Entry]\nType=Application\nName=Hidden\nExec=x\nNoDisplay=true\n")
            app = parse_desktop_file(path)
            self.assertEqual(app.name, "Calculator")
            self.assertIsNone(parse_desktop_file(hidden))
            index = DesktopAppIndex()
            index._apps, index._loaded = [app], True
            self.assertEqual(index.best_match("calculator").name, "Calculator")
            self.assertEqual(index.best_match("calc").name, "Calculator")
            self.assertIsNone(index.best_match("photoshop"))


class MiscTests(unittest.TestCase):
    def test_state_machine(self):
        sm = StateMachine(SyraLogger())
        self.assertTrue(sm.transition(State.ACTIVE))
        self.assertTrue(sm.transition(State.PROCESSING))
        self.assertTrue(sm.transition(State.SPEAKING))
        self.assertTrue(sm.transition(State.ACTIVE))
        self.assertTrue(sm.transition(State.IDLE))
        sm2 = StateMachine(SyraLogger())
        self.assertFalse(sm2.transition(State.PROCESSING))       # IDLE -> PROCESSING is illegal

    def test_split_for_speech(self):
        self.assertEqual(len(split_for_speech(cfg.GREETING)), 1)
        long = " ".join(["This is a reasonably long sentence about something interesting."] * 6)
        chunks = split_for_speech(long)
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(c) <= 250 for c in chunks))

    def test_restricted_shell(self):
        shell = RestrictedShell({"say hi": ["echo", "hi"]}, 5, 100)
        self.assertEqual(shell.run("Say Hi").output, "hi")
        self.assertFalse(shell.run("rm -rf /").ok)

    def test_copy_template(self):
        with tempfile.TemporaryDirectory() as tmp:
            src, dst = Path(tmp) / "src", Path(tmp) / "dst"
            src.mkdir()
            (src / "_PLACEHOLDER.md").write_text("x")
            self.assertFalse(copy_template(src, dst))
            dst2 = Path(tmp) / "dst2"
            (src / "index.html").write_text("<html></html>")
            self.assertTrue(copy_template(src, dst2))
            self.assertTrue((dst2 / "index.html").exists())
            self.assertFalse((dst2 / "_PLACEHOLDER.md").exists())


if __name__ == "__main__":
    unittest.main()
