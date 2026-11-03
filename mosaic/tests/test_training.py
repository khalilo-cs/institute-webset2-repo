import json
import tempfile
import unittest
from pathlib import Path

from mosaic.knowledge import save_chat_feedback
from mosaic.training import export_consented_feedback
from mosaic.workspace import add_message, create_conversation


class TrainingExportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = str(Path(self.tmp.name) / "mosaic.sqlite3")
        self.chat = create_conversation(self.db)

    def tearDown(self):
        self.tmp.cleanup()

    def add_example(self, index, allow_training, conversation_id=None):
        conversation_id = conversation_id or self.chat["id"]
        user = add_message(
            self.db, conversation_id, "user",
            f"اشرح مفهوم الاختبار رقم {index}"
        )
        assistant = add_message(
            self.db, conversation_id, "assistant",
            "إجابة أولية تحتاج تحسيناً"
        )
        result = save_chat_feedback(
            self.db, assistant["id"], "not_accurate",
            f"التصحيح الموثق للمفهوم رقم {index}",
            allow_training=allow_training
        )
        self.assertEqual(result["allow_training"], allow_training)
        return user, assistant

    def test_export_uses_only_explicitly_consented_corrections(self):
        self.add_example(0, False)
        for index in range(1, 11):
            self.add_example(index, True)
        output = Path(self.tmp.name) / "training-data"
        result = export_consented_feedback(self.db, str(output))
        self.assertEqual(result["total_examples"], 10)
        self.assertEqual(result["train_examples"] + result["eval_examples"], 10)
        self.assertGreaterEqual(result["eval_examples"], 2)
        train = [json.loads(line) for line in (output / "train.jsonl").read_text(encoding="utf-8").splitlines()]
        evaluate = [json.loads(line) for line in (output / "eval.jsonl").read_text(encoding="utf-8").splitlines()]
        all_rows = train + evaluate
        self.assertEqual(len(all_rows), 10)
        all_targets = [item["messages"][-1]["content"] for item in all_rows]
        self.assertNotIn("التصحيح الموثق للمفهوم رقم 0", all_targets)
        self.assertIn("التصحيح الموثق للمفهوم رقم 10", all_targets)
        self.assertEqual(json.loads((output / "manifest.json").read_text())["automatic_training"], False)

    def test_export_without_opted_in_feedback_is_empty(self):
        self.add_example(1, False)
        result = export_consented_feedback(self.db, str(Path(self.tmp.name) / "empty"))
        self.assertEqual(result["total_examples"], 0)
        self.assertEqual(result["train_examples"], 0)
        self.assertEqual(result["eval_examples"], 0)


    def test_heldout_split_keeps_conversations_separate(self):
        second_chat = create_conversation(self.db)
        train_chat = self.chat["id"]
        eval_chat = second_chat["id"]
        for index in range(6):
            self.add_example(index, True, train_chat)
        for index in range(6, 12):
            self.add_example(index, True, eval_chat)
        output = Path(self.tmp.name) / "conversation-split"
        result = export_consented_feedback(self.db, str(output))
        self.assertEqual(result["split_strategy"], "conversation")
        train_rows = [json.loads(line) for line in (output / "train.jsonl").read_text(encoding="utf-8").splitlines()]
        eval_rows = [json.loads(line) for line in (output / "eval.jsonl").read_text(encoding="utf-8").splitlines()]
        con = __import__("sqlite3").connect(self.db)
        try:
            ids = [row["message_id"] for row in train_rows + eval_rows]
            lookup = dict(con.execute(
                "SELECT id,conversation_id FROM chat_messages WHERE role='assistant'"
            ).fetchall())
        finally:
            con.close()
        train_conversations = {lookup[row["message_id"]] for row in train_rows}
        eval_conversations = {lookup[row["message_id"]] for row in eval_rows}
        self.assertTrue(train_conversations.isdisjoint(eval_conversations))
        self.assertEqual(train_conversations | eval_conversations, {train_chat, eval_chat})


    def test_withdrawal_invalidates_prepared_dataset(self):
        _, assistant = self.add_example(21, True)
        output = Path(self.tmp.name) / "revocation-data"
        export_consented_feedback(self.db, str(output))
        self.assertTrue((output / "manifest.json").exists())
        save_chat_feedback(
            self.db, assistant["id"], "not_accurate",
            "التصحيح الموثق للمفهوم رقم 21", allow_training=False
        )
        self.assertFalse((output / "manifest.json").exists())
        self.assertFalse((output / "train.jsonl").exists())
        self.assertFalse((output / "eval.jsonl").exists())

if __name__ == "__main__":
    unittest.main()
