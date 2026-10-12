import tempfile
import unittest

from feishu_agent_bridge.config import Config
from feishu_agent_bridge.store import Store
from tests.helpers import message, settings


class StateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_configuration_has_positive_intervals_and_no_personal_defaults(self):
        config = Config.from_dict(settings(self.tmp.name))
        self.assertIsNotNone(config, "configuration must be validated and materialized")
        self.assertEqual(config.poll_seconds, 30)
        self.assertEqual(config.read_seconds, 60)
        for changes in ({"poll_seconds": 0}, {"profile": ""}, {"bot_open_id": "cli_wrong"},
                        {"groups": []}, {"auto_handoff": True}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                Config.from_dict(settings(self.tmp.name, **changes))

    def test_ordinary_message_is_persisted_without_mention(self):
        config = Config.from_dict(settings(self.tmp.name))
        store = Store(config)
        self.assertTrue(store.ingest(message()), "ordinary messages must be collected too")
        self.addCleanup(store.close)
        self.assertEqual(store.pending_messages("oc_team")[0]["content"], "普通群消息")

    def test_true_mention_priority_and_display_name_not_mention(self):
        store = Store(Config.from_dict(settings(self.tmp.name)))
        self.assertTrue(store.ingest(message("om_plain", content="@皮卡丘", create_time="1100")))
        self.addCleanup(store.close)
        store.ingest(message("om_at", mentions=[{"id": "ou_self"}]))
        self.assertFalse(store.get_message("om_plain")["addressed"])
        self.assertTrue(store.get_message("om_at")["addressed"])
        self.assertEqual(store.pending_messages("oc_team")[0]["message_id"], "om_at")

    def test_scope_malformed_timestamp_and_old_message_rejected(self):
        store = Store(Config.from_dict(settings(self.tmp.name)))
        self.assertTrue(store.ingest(message("om_valid")))
        self.addCleanup(store.close)
        for change in ({"chat_id": "oc_private"}, {"sender_type": "unknown"},
                       {"create_time": "bad"}, {"create_time": "999"},
                       {"message_id": "../../bad"}, {"mentions": "ou_self"}):
            with self.subTest(change=change):
                self.assertFalse(store.ingest(message("om_invalid", **change)))

    def test_self_messages_are_visible_but_not_pending(self):
        store = Store(Config.from_dict(settings(self.tmp.name)))
        self.assertTrue(store.ingest(message(sender_id="cli_self", sender_type="bot")))
        self.addCleanup(store.close)
        self.assertTrue(store.get_message("om_one")["own"])
        self.assertEqual(store.pending_messages("oc_team"), [])

    def test_realtime_duplicate_and_new_edit_version_survive_restart(self):
        config = Config.from_dict(settings(self.tmp.name))
        store = Store(config)
        self.assertTrue(store.ingest(message()))
        store.mark_read(["om_one"])
        store.close()
        store = Store(config)
        self.addCleanup(store.close)
        self.assertFalse(store.ingest(message(event_id="different")))
        self.assertEqual(store.pending_messages("oc_team"), [])
        self.assertTrue(store.ingest(message(content="edited", update_time="3000")))
        self.assertEqual(store.pending_messages("oc_team")[0]["content"], "edited")

    def test_identity_binding_cannot_reuse_database_for_other_bot(self):
        store = Store(Config.from_dict(settings(self.tmp.name)))
        self.assertTrue(store.ingest(message()))
        store.close()
        with self.assertRaises(ValueError):
            Store(Config.from_dict(settings(self.tmp.name, profile="unrelated-profile")))

    def test_interrupted_read_recovers_but_interrupted_send_is_uncertain(self):
        config = Config.from_dict(settings(self.tmp.name))
        store = Store(config)
        self.assertTrue(store.ingest(message()))
        store.reserve_messages(["om_one"])
        rid = store.enqueue_reply("om_one", "final", "回答", [])
        store.claim_reply(rid)
        store.close()
        store = Store(config, recover=True)
        self.addCleanup(store.close)
        self.assertEqual(len(store.pending_messages("oc_team")), 1)
        self.assertEqual(store.reply(rid)["state"], "uncertain")
        self.assertEqual(store.pending_replies(), [])

    def test_task_claim_completion_and_final_reply_are_idempotent(self):
        store = Store(Config.from_dict(settings(self.tmp.name)))
        self.assertTrue(store.ingest(message()))
        self.addCleanup(store.close)
        task = store.create_task("om_one", "修改 SPI", "请由开发会话核对 lease 后修改")
        self.assertTrue(store.claim_task(task, "main-thread"))
        self.assertFalse(store.claim_task(task, "another-thread"))
        self.assertTrue(store.complete_task(task, "main-thread", "已完成验证"))
        self.assertFalse(store.complete_task(task, "main-thread", "重复结果"))
        self.assertEqual(len(store.pending_replies()), 1)


if __name__ == "__main__":
    unittest.main()
