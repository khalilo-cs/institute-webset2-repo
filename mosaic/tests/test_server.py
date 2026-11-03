import unittest

from mosaic.server import compose_chat_user_content


class ChatContentCompositionTests(unittest.TestCase):
    def test_text_only_chat_with_retrieved_context_stays_a_string(self):
        content = compose_chat_user_content("ما معنى RAG؟", [], "RAG يسترجع المعرفة")
        self.assertIsInstance(content, str)
        self.assertIn("RAG يسترجع المعرفة", content)

    def test_multimodal_chat_keeps_attachments_and_appends_context_block(self):
        image = {"type": "image_url", "image_url": {"url": "data:image/png;base64,AA=="}}
        content = compose_chat_user_content("اشرح الصورة", [image], "ملاحظة من ملف سابق")
        self.assertIsInstance(content, list)
        self.assertEqual(content[0], {"type": "text", "text": "اشرح الصورة"})
        self.assertEqual(content[1], image)
        self.assertEqual(content[2], {"type": "text", "text": "سياق مسترجع:\nملاحظة من ملف سابق"})

    def test_multimodal_chat_without_context_keeps_attachments(self):
        image = {"type": "image_url", "image_url": {"url": "data:image/png;base64,AA=="}}
        content = compose_chat_user_content("اشرح الصورة", [image])
        self.assertEqual(content, [{"type": "text", "text": "اشرح الصورة"}, image])


if __name__ == "__main__":
    unittest.main()
