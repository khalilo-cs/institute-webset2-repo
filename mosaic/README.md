# MOSAIC — نواة التعلم المستمر والاستقلال عن النماذج المعلّمة

MOSAIC يتعلم من نماذج ذكاء اصطناعي أخرى (المعلّمين) ومن البشر، ثم يثبت ما تعلّمه على مهام مستقلة لم يرها. عندما يصل أداؤه إلى مستوى المعلّم في فئة ما **يتخرج فيها**: يتوقف عن استدعاء المعلّم لهذه الفئة ويعمل وحده، حتى بدون إنترنت.

## التشغيل

لا يحتاج إلى أي مكتبات خارجية. يكفي Python 3.10 أو أحدث.

```bash
cd mosaic
python -m mosaic demo                 # عرض كامل في الطرفية: تعلم ← إثبات ← استقلال
python -m mosaic serve --demo         # الواجهة على http://127.0.0.1:8765
python -m mosaic serve --db my.db     # نسخة فارغة لبياناتك
ANTHROPIC_API_KEY=... python -m mosaic serve --anthropic   # معلّم حقيقي (Claude)
python -m unittest discover -s tests -t .                  # الاختبارات
```

## ماذا يحدث في العرض

| الجولة | ما يحدث |
|--------|---------|
| 1 | المهام تذهب للمعلّم. MOSAIC يجمع إجاباته ويستنتج منها برامج (مهارات) ويختبرها على أمثلة مستقلة. يعتمد الناجح منها ويتخرج في 4 فئات من 5 |
| 2–3 | فئة المعرفة (العواصم) تتحسن من 67% إلى 96% مع انخفاض الاعتماد على المعلّم من 14 إلى 1 من كل 15 مهمة، ثم تتخرج |
| 4 فما بعد | صفر استدعاءات للمعلّم. كل المهام يحلها MOSAIC وحده |

## المكونات

| الملف | المسؤولية |
|-------|-----------|
| `memory.py` | ثمانية مخازن على SQLite: الدلالية، الخبرات، المهارات، الأدوات، التجارب، الأخطاء، التفضيلات (بموافقة فقط)، سجل التغييرات (لا يقبل التعديل). تتضمن إزالة التكرار وكشف التعارض والأرشفة |
| `dsl.py` | لغة مهارات آمنة (بيانات وليست كودًا قابلًا للتنفيذ) وتركيب البرامج من الأمثلة، مع تحمّل أخطاء المعلّم |
| `skills.py` | سجل مهارات بإصدارات، وتفعيل وتراجع. المهارة المعتمدة تصبح **أداة** قابلة لإعادة الاستخدام (Tool Evolution) |
| `models.py` | سجل النماذج والمعلّمين، وقياس دقتهم الفعلية، واحترام الترخيص والخصوصية والميزانية |
| `core.py` | محرك التعلم المستمر، ومختبر التطور، وحارس التراجع، والتخرج، والتعلم من البشر، وبيانات اللوحة |
| `scheduler.py` | جدولة واعية بالموارد: أولويات، بطارية، شحن، شبكة، إيقاف واستئناف وإلغاء |
| `server.py` و`static/` | واجهة ويب عربية تعمل من متصفح الهاتف، وهي نفس الـ API الذي سيتصل به تطبيق Android |

## الضمانات المطبقة في الكود (ومغطاة بالاختبارات)

- أمثلة الاختبار المستقل **لا تُرسل أبدًا** إلى أي معلّم (`test_heldout_never_reaches_teacher`).
- لا تُعتمد مهارة إلا بتحسن ذي دلالة إحصائية على الاختبار المستقل، ودون تراجع في الفئات التي تستخدمها كأداة.
- معرفة المعلّم تبدأ بثقة 0.6، ولا تُستخدم في الإجابة حتى يعتمدها المختبر.
- لا يتدرب على مخرجات نموذج إن كان ترخيصه غير معروف (`test_training_blocked_when_license_unknown`).
- لا استدعاءات للمعلّم بدون اتصال، ولا تُسجَّل معلومة على أنها من الإنترنت وهي ليست كذلك.
- بعد التخرج لا يُستدعى المعلّم إلا إذا أعاد المستخدم السماح بذلك يدويًا.
- الرأي الشخصي لا يتحول إلى حقيقة، والبيانات غير الموثوقة تُعزل في طابور التحقق.
- بيانات كل مستخدم معزولة: ملاحظات مستخدم لا تدخل المهارات المشتركة.
- سجل التغييرات لا يقبل التعديل أو الحذف (مفروض بقيود في قاعدة البيانات).
- بعد فشل متكرر يتجمد الهدف ولا يستهلك موارد حتى يستأنفه المستخدم.

## حدود صريحة

- هذه **نواة تعلم حقيقية تعمل**، لكنها ليست نموذجًا لغويًا عامًا. تكتسب الآن نوعين من القدرات: مهارات تحويل (برامج تُستنتج من الأمثلة) ومعرفة (سؤال وجواب). كل فئة جديدة تضيفها تتعلمها بالآلية نفسها، ولا يوجد حد لعدد الفئات.
- الاسترجاع يستخدم تمثيلًا نصيًا خفيفًا بلا شبكة عصبية. يمكن استبداله بـ Embedder عصبي عبر الواجهة نفسها.
- التكييف الفعلي لأوزان النماذج (النوع E: LoRA وما شابهها) غير مُنفّذ بعد. هو المرحلة P5 في البرومبت.
- تطبيق Android الأصلي لم يُبنَ هنا لأن بيئة البناء لا تحتوي Android SDK. النواة مكتوبة بـ Python القياسية بلا اعتماديات حتى تعمل على الهاتف عبر Chaquopy أو Termux، والواجهة تعمل الآن من متصفح الهاتف.
- الخادم مخصص للتشغيل المحلي (يستمع على 127.0.0.1) ولا يحتوي على تسجيل دخول بعد.


## MOSAIC platform roadmap and current workspace foundation

The product direction is **Android + Windows**, with a **hybrid local/cloud model strategy** and universal file intake.

### Implemented in the workspace foundation
- Persistent conversations and messages in SQLite, with conversation titles, archive/rename/delete API operations.
- Chat workspace UI with conversation history, new conversations, message composer and attachments.
- Arbitrary file extensions can be uploaded and stored as opaque binary attachments (20 MiB per file). Files are never executed.
- Bounded text extraction for common text/code formats; best-effort extraction for Office Open XML and PDF when a PDF parser is installed; archive manifest listing for ZIP/JAR/APK.
- File SHA-256 fingerprints, MIME detection, per-file metadata and local storage.
- Hybrid OpenAI-compatible chat provider routing: local endpoint is preferred; cloud is fallback. Private chats are local-only.
- Windows desktop shell using the same local server and UI; Windows packaging workflow creates `MOSAIC.exe`.
- Android WebView continues to use the same Python server and web UI.

### Configure the hybrid model router (Windows / local server)

Local-first (for example, Ollama exposing an OpenAI-compatible API):

```powershell
cd mosaic
$env:MOSAIC_LOCAL_BASE_URL="http://127.0.0.1:11434/v1"
$env:MOSAIC_LOCAL_MODEL="qwen2.5:7b"
python -m mosaic serve --db "$env:LOCALAPPDATA\\MOSAIC\\mosaic.sqlite3"
```

Cloud fallback (compatible endpoint and credentials supplied by the operator):

```powershell
$env:MOSAIC_CLOUD_BASE_URL="https://your-provider.example/v1"
$env:MOSAIC_CLOUD_API_KEY="set-this-in-your-secret-store"
$env:MOSAIC_CLOUD_MODEL="your-model-id"
```

Do not commit keys, put them in the APK, or paste them into source files. The Windows shell currently reads provider configuration from its process environment. A secure in-app credential vault for Android is a separate upcoming milestone.

### File support: honest compatibility contract
"Accept all files" means MOSAIC accepts and safely stores any filename/extension as a non-executable attachment, subject to the 20 MiB limit. It does **not** claim that every file can already be semantically understood. Text/code formats are extracted; Office Open XML and PDF use best-effort parsers; images need OCR/vision, audio needs speech transcription, video needs frame/audio extraction, and proprietary/binary formats need dedicated adapters. Unsupported files remain downloadable attachments with metadata and hashes.

### Security boundaries
- Never execute uploaded files, scripts, macros or archive contents.
- File size is limited; archive extraction is bounded by member count and expanded byte limits.
- Treat file and website content as untrusted prompt data.
- Private chats use only the configured local provider; they do not fall back to cloud.
- The current local HTTP server binds to loopback by default. Do not expose it to a network without authentication, TLS and authorization.

### Next milestones
1. Secure provider settings and model status UI for Windows and Android.
2. Multimodal adapters: image understanding/OCR, PDF page rendering, audio transcription, video frame extraction.
3. Image and video generation providers with real job status, outputs and usage limits.
4. Cross-device account sync, encrypted backups and conversation search.
5. Permissioned device search through Android Storage Access Framework and indexed user-selected folders.


### Multimodal attachment analysis (current increment)
- Chat and the dedicated file-question endpoint now pass extracted text plus bounded image data URLs to the configured OpenAI-compatible model.
- Supported direct image MIME types for model forwarding: PNG, JPEG, WebP and GIF.
- Limits: at most 4 images and 8 MiB aggregate image payload per request; each image is capped at 5 MiB for forwarding. Larger images remain stored and downloadable.
- The selected local/cloud model must itself support vision. A text-only model may reject image content; MOSAIC does not pretend to have OCR/vision when the configured model lacks it.
- Arbitrary non-image binary files are not forwarded as raw bytes and are never executed. Their supported extracted text can still be analyzed.
- Added tests for image data URL construction and ensuring unknown binary payloads are not forwarded.


## التعلم من ملفات المستخدم واسترجاع المعرفة
- أرفق ملفاتك في المحادثة، ثم اضغط **«تعلم من الملفات المحددة»** لحفظ النص المستخرج في قاعدة معرفة محلية.
- تُقسّم النصوص إلى مقاطع متداخلة، مع حفظ اسم الملف وبصمة SHA-256 ورقم المقطع كمصدر.
- عند السؤال في محادثة غير خاصة، يسترجع MOSAIC المقاطع الأقرب إلى السؤال ويضيفها إلى سياق النموذج؛ وتظهر أسماء المصادر في الرد.
- واجهات API: `POST /api/knowledge/ingest` و`POST /api/knowledge/search` و`POST /api/knowledge/forget`.
- يمكن حذف معرفة ملف من الفهرس دون حذف الملف الأصلي عبر واجهة `/api/knowledge/forget`.
- لا يُفهرس الملف تلقائياً بمجرد رفعه؛ يجب الضغط على زر التعلم. الملفات التي لا يتوفر لها محلل نصي تُحفظ كمرفقات فقط، ولا تُعد معرفة مفهومة.
- هذه آلية ذاكرة واسترجاع (RAG)، وليست تدريباً فورياً لأوزان النموذج. لا يمكن ضمان صحة كل مستند؛ يجب التحقق من المصادر والتعارضات.

## معنى «أي شيء وبدون حدود»
MOSAIC مصمم لقبول امتدادات الملفات المختلفة وتوسيع المحللات والأدوات تدريجياً، لكنه لا يستطيع فهم كل صيغة لمجرد قبولها. الملفات التنفيذية والمجهولة لا تُشغّل. إجابات النموذج تعتمد على المزود المتصل وسياقه وقدراته وسياسات الاستخدام والموارد المتاحة. لا يوجد نظام واقعي يضمن إجابة صحيحة عن كل سؤال أو قدرات غير محدودة. الهدف هو تغطية واسعة قابلة للاختبار، مع إعلان عدم اليقين، وإذن المستخدم قبل الأفعال الخارجية أو التغييرات ذات الأثر.
