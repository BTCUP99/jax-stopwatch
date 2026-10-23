import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from xwatch import describe


class DescribeTests(unittest.TestCase):
    def test_inbound_follow_in_both_documented_shapes(self):
        for wrap in (lambda u: {"data": u}, lambda u: u):
            with self.subTest(wrapped=wrap({}) != {}):
                event = {"event_type": "follow.follow", "payload": {
                    "source": wrap({"id": "7", "name": "小明", "username": "xm"}), "target": wrap({"id": "1"})}}
                self.assertEqual(describe(event, "1"), ("follow", "新粉丝：小明（@xm）"))

    def test_own_outbound_follow_is_ignored(self):
        event = {"event_type": "follow.follow", "payload": {"source": {"id": "1"}, "target": {"id": "7"}}}
        self.assertIsNone(describe(event, "1"))

    def test_mention_strips_handles_and_links(self):
        event = {"event_type": "post.mention.create",
                 "payload": {"author_id": "9", "text": "@me @other 看这个 https://t.co/abc"},
                 "includes": {"users": [{"id": "9", "name": "Ann", "username": "ann"}]}}
        self.assertEqual(describe(event, "1"), ("mention", "Ann（@ann） 提到了你：看这个"))

    def test_reply_to_own_post(self):
        event = {"event_type": "post.reply.create",
                 "payload": {"author_id": "9", "text": "@me 同意"},
                 "includes": {"users": [{"id": "9", "name": "Ann", "username": "ann"}]}}
        self.assertEqual(describe(event, "1"), ("reply", "Ann（@ann） 回复了你：同意"))

    def test_repost_and_quote(self):
        users = {"users": [{"id": "9", "name": "Ann", "username": "ann"}]}
        repost = {"event_type": "post.repost.create", "payload": {"author_id": "9", "text": "RT @me: 原文"},
                  "includes": users}
        quote = {"event_type": "post.quote.create", "payload": {"author_id": "9", "text": "说得好 https://t.co/q"},
                 "includes": users}
        self.assertEqual(describe(repost, "1"), ("repost", "Ann（@ann） 转发了你的帖子"))
        self.assertEqual(describe(quote, "1"), ("quote", "Ann（@ann） 引用了你的帖子：说得好"))

    def test_own_post_mentioning_self_is_ignored(self):
        event = {"event_type": "post.mention.create", "payload": {"author_id": "1", "text": "@me hi"}}
        self.assertIsNone(describe(event, "1"))


if __name__ == "__main__":
    unittest.main()
