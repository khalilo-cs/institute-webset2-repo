import base64
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


from mosaic.workspace import (
    add_message, archive_conversation, create_conversation, delete_conversation, get_conversation,
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


    def test_archive_and_restore_conversation(self):
        self.assertTrue(archive_conversation(self.db, self.chat["id"], True))
        self.assertEqual(len(list_conversations(self.db)), 0)
        self.assertEqual(len(list_conversations(self.db, include_archived=True)), 1)
        self.assertTrue(archive_conversation(self.db, self.chat["id"], False))
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



    def test_text_attachment_preserves_real_line_breaks(self):
        raw = "السطر الأول\nالسطر الثاني".encode("utf-8")
        item = store_file(self.db, "multiline.txt", base64.b64encode(raw).decode(), self.chat["id"])
        blocks = build_attachment_content(self.db, [item["id"]])
        self.assertIn("السطر الأول\nالسطر الثاني", blocks[0]["text"])


    def test_image_ingestion_uses_vision_and_persists_searchable_summary(self):
        from mosaic.knowledge import ingest_file, search_knowledge
        raw = b"fake-image-bytes"
        item = store_file(self.db, "diagram.png", base64.b64encode(raw).decode(), self.chat["id"])
        with patch("mosaic.providers.chat_completion",
                   return_value=("مخطط يوضح انتقال البيانات بين الخادم وقاعدة البيانات.", "local:vision-test")) as model:
            result = ingest_file(self.db, item["id"], private=True)
        self.assertEqual(result["status"], "indexed_vision")
        self.assertEqual(result["content_kind"], "vision_summary")
        self.assertTrue(result["vision_attempted"])
        model.assert_called_once()
        self.assertTrue(model.call_args.kwargs["private"])
        found = search_knowledge(self.db, "انتقال البيانات قاعدة البيانات")
        self.assertTrue(found)
        self.assertEqual(found[0]["kind"], "vision_summary")
        self.assertEqual(found[0]["provider"], "local:vision-test")

    def test_image_ingestion_reports_missing_vision_model_without_fake_learning(self):
        from mosaic.knowledge import ingest_file, search_knowledge
        from mosaic.providers import ProviderUnavailable
        raw = b"fake-image-bytes"
        item = store_file(self.db, "scan.jpg", base64.b64encode(raw).decode(), self.chat["id"])
        with patch("mosaic.providers.chat_completion", side_effect=ProviderUnavailable("not configured")):
            result = ingest_file(self.db, item["id"], private=True)
        self.assertEqual(result["status"], "vision_unavailable")
        self.assertEqual(result["indexed"], 0)
        self.assertEqual(search_knowledge(self.db, "anything visible"), [])

    def test_image_ingestion_can_be_bounded_without_calling_vision(self):
        from mosaic.knowledge import ingest_file
        item = store_file(self.db, "extra.webp", base64.b64encode(b"fake-image").decode(), self.chat["id"])
        with patch("mosaic.providers.chat_completion") as model:
            result = ingest_file(self.db, item["id"], allow_vision=False)
        self.assertEqual(result["status"], "vision_limit_reached")
        model.assert_not_called()

    def test_knowledge_ingestion_search_and_forget(self):
        from mosaic.knowledge import forget_file_knowledge, ingest_file, search_knowledge, knowledge_stats
        raw = "MOSAIC learns from approved documents and retrieves source passages.\n" * 80
        item = store_file(self.db, "research.txt", base64.b64encode(raw.encode()).decode(), self.chat["id"])
        result = ingest_file(self.db, item["id"])
        self.assertGreater(result["indexed"], 1)
        found = search_knowledge(self.db, "approved documents retrieves")
        self.assertTrue(found)
        self.assertEqual(found[0]["source"], "research.txt")
        self.assertEqual(knowledge_stats(self.db)["files"], 1)
        self.assertGreater(forget_file_knowledge(self.db, item["id"]), 0)
        self.assertEqual(search_knowledge(self.db, "approved documents"), [])

    def test_conversation_delete_removes_history(self):
        add_message(self.db, self.chat["id"], "user", "delete me")
        self.assertTrue(delete_conversation(self.db, self.chat["id"]))
        self.assertIsNone(get_conversation(self.db, self.chat["id"]))


if __name__ == "__main__":
    unittest.main()
