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

    def add_example(self, index, allow_training):
        user = add_message(
            self.db, self.chat["id"], "user",
            f"اشرح مفهوم الاختبار رقم {index}"
        )
        assistant = add_message(
            self.db, self.chat["id"], "assistant",
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


if __name__ == "__main__":
    unittest.main()
