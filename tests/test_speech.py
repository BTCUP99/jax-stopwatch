import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from speech import hallucinated, prompt

SPEECH = [{"no_speech_prob": .05, "avg_logprob": -.3}]


class HallucinationTests(unittest.TestCase):
    def test_prompt_carries_name_and_assistants(self):
        self.assertEqual(prompt("贾克斯"), "简体中文。贾克斯。Codex。Grok Bot。编程助手。")

    def test_echoed_prompt_is_rejected(self):
        for text in ("编程助手。Codex。Grok Bot。编程助手。", "Codex。Grok Bot。", "Codex。Codex。", "简体中文。"):
            with self.subTest(text=text):
                self.assertTrue(hallucinated(text, SPEECH, "贾克斯"))

    def test_stock_subtitle_phrases_are_rejected(self):
        self.assertTrue(hallucinated("字幕by索兰娅", SPEECH, "贾克斯"))
        self.assertTrue(hallucinated("请不吝点赞 订阅 转发", SPEECH))

    def test_low_speech_confidence_is_rejected(self):
        self.assertTrue(hallucinated("转发。", [{"no_speech_prob": .62, "avg_logprob": -.4}]))

    def test_real_commands_survive(self):
        for text in ("贾克斯。", "贾克斯。Codex。", "切换到Grok。", "Codex。", "贾克斯，贾克斯，让编程助手帮我记待办。",
                     "帮我给这条点赞"):
            with self.subTest(text=text):
                self.assertFalse(hallucinated(text, SPEECH, "贾克斯"))
