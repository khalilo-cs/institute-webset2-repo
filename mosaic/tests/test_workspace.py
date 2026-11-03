import base64
import tempfile
import unittest
from pathlib import Path

from mosaic.workspace import (
    add_message, create_conversation, delete_conversation, get_conversation,
    list_conversations, list_files, store_file,
)


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = str(Path(self.tmp.name) / "mosaic.sqlite3")
        self.chat = create_conversation(self.db)

    def tearDown(self):
        self.tmp.cleanup()

    def test_conversation_persists_messages_and_updates_title(self):
        add_message(self.db, self.chat["id"], "user", "مرحبا MOSAIC")
        add_message(self.db, self.chat["id"], "assistant", "أهلاً")
        item = get_conversation(self.db, self.chat["id"])
        self.assertEqual(item["title"], "مرحبا MOSAIC")
        self.assertEqual([m["role"] for m in item["messages"]], ["user", "assistant"])
        self.assertEqual(len(list_conversations(self.db)), 1)

    def test_accepts_unknown_binary_extension_without_execution(self):
        raw = b"\x00\x01not-an-executable-payload"
        item = store_file(self.db, "sample.weird-blob", base64.b64encode(raw).decode(), self.chat["id"])
        self.assertEqual(item["status"], "stored_opaque")
        self.assertEqual(item["parser"], "opaque-binary")
        self.assertEqual(item["size_bytes"], len(raw))
        self.assertEqual(len(list_files(self.db, self.chat["id"])), 1)

    def test_text_file_is_extracted(self):
        raw = "MOSAIC can read this Arabic text.".encode()
        item = store_file(self.db, "notes.txt", base64.b64encode(raw).decode(), self.chat["id"])
        self.assertEqual(item["parser"], "utf8-text")
        self.assertGreater(item["extracted_characters"], 10)

    def test_conversation_delete_removes_history(self):
        add_message(self.db, self.chat["id"], "user", "delete me")
        self.assertTrue(delete_conversation(self.db, self.chat["id"]))
        self.assertIsNone(get_conversation(self.db, self.chat["id"]))


if __name__ == "__main__":
    unittest.main()
