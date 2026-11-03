import base64
import tempfile
import unittest
from pathlib import Path

from mosaic.workspace import (
    add_message, create_conversation, delete_conversation, get_conversation,
    build_attachment_content, list_conversations, list_files, search_conversations, store_file,
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

    def test_searches_conversation_messages(self):
        add_message(self.db, self.chat["id"], "user", "Find my database notes")
        self.assertEqual(len(search_conversations(self.db, "database")), 1)
        self.assertEqual(len(search_conversations(self.db, "not-present")), 0)

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


    def test_image_attachment_becomes_multimodal_data_url(self):
        raw = b"fake-png-bytes"
        item = store_file(self.db, "diagram.png", base64.b64encode(raw).decode(), self.chat["id"])
        blocks = build_attachment_content(self.db, [item["id"]])
        self.assertEqual(blocks[0]["type"], "image_url")
        self.assertTrue(blocks[0]["image_url"]["url"].startswith("data:image/png;base64,"))
        self.assertEqual(blocks[1]["type"], "text")

    def test_unknown_binary_is_not_forwarded_to_model(self):
        raw = b"opaque bytes"
        item = store_file(self.db, "payload.unknown", base64.b64encode(raw).decode(), self.chat["id"])
        self.assertEqual(build_attachment_content(self.db, [item["id"]]), [])

    def test_conversation_delete_removes_history(self):
        add_message(self.db, self.chat["id"], "user", "delete me")
        self.assertTrue(delete_conversation(self.db, self.chat["id"]))
        self.assertIsNone(get_conversation(self.db, self.chat["id"]))


if __name__ == "__main__":
    unittest.main()
