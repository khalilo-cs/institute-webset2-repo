package ai.mosaic.mobile;

import android.app.Activity;
import android.content.Intent;
import android.content.IntentFilter;
import android.content.Context;
import android.net.ConnectivityManager;
import android.os.BatteryManager;
import android.os.Build;
import android.os.Bundle;
import android.os.PowerManager;
import android.speech.RecognitionListener;
import android.speech.RecognizerIntent;
import android.speech.SpeechRecognizer;
import android.speech.tts.TextToSpeech;
import android.content.pm.PackageManager;
import android.app.DownloadManager;
import android.os.Environment;
import android.Manifest;
import android.webkit.JavascriptInterface;
import android.net.Uri;
import android.webkit.ValueCallback;
import android.webkit.WebChromeClient;
import java.util.ArrayList;
import java.util.Locale;
import android.view.Gravity;
import android.view.View;
import android.view.Window;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Button;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.ProgressBar;
import android.widget.TextView;

import com.chaquo.python.android.AndroidPlatform;
import com.chaquo.python.PyObject;
import com.chaquo.python.Python;

import org.json.JSONObject;

import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

public final class MainActivity extends Activity {
    private final ExecutorService io = Executors.newSingleThreadExecutor();
    private WebView webView;
    private TextView statusText;
    private LinearLayout statusPanel;
    private ProgressBar progress;
    private String baseUrl;
    private SpeechRecognizer speechRecognizer;
    private TextToSpeech textToSpeech;
    private boolean pendingSpeechStart = false;
    private ValueCallback<Uri[]> fileCallback;
    private static final int REQUEST_AUDIO = 7401;
    private static final int REQUEST_FILES = 7402;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        getWindow().setStatusBarColor(0xFF10161D);
        getWindow().setNavigationBarColor(0xFF10161D);
        if (Build.VERSION.SDK_INT >= 30) {
            getWindow().setDecorFitsSystemWindows(true);
        }

        buildScreen();
        textToSpeech = new TextToSpeech(this, status -> {
            if (status == TextToSpeech.SUCCESS && textToSpeech != null) textToSpeech.setLanguage(new Locale("ar"));
        });
        startMosaic();
    }

    private void buildScreen() {
        FrameLayout root = new FrameLayout(this);
        root.setBackgroundColor(0xFF10161D);

        webView = new WebView(this);
        webView.setVisibility(View.GONE);
        webView.setBackgroundColor(0xFF10161D);

        WebSettings settings = webView.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);
        settings.setAllowFileAccess(false);
        settings.setAllowContentAccess(true); // SAF content:// URIs are granted only after the user selects files.
        settings.setMixedContentMode(WebSettings.MIXED_CONTENT_NEVER_ALLOW);
        settings.setSupportZoom(false);

        webView.addJavascriptInterface(new MosaicJsBridge(), "AndroidMosaic");
        webView.setWebChromeClient(new WebChromeClient() {
            @Override
            public boolean onShowFileChooser(WebView view, ValueCallback<Uri[]> callback,
                                             FileChooserParams params) {
                if (fileCallback != null) fileCallback.onReceiveValue(null);
                fileCallback = callback;
                Intent intent = new Intent(Intent.ACTION_OPEN_DOCUMENT);
                intent.addCategory(Intent.CATEGORY_OPENABLE);
                intent.setType("*/*");
                intent.putExtra(Intent.EXTRA_ALLOW_MULTIPLE, true);
                try {
                    startActivityForResult(intent, REQUEST_FILES);
                    return true;
                } catch (Exception e) {
                    fileCallback = null;
                    return false;
                }
            }
        });

        webView.setDownloadListener((url, userAgent, contentDisposition, mimeType, contentLength) -> {
            try {
                DownloadManager manager = (DownloadManager) getSystemService(DOWNLOAD_SERVICE);
                if (manager == null) {
                    statusText.setText("تعذر بدء تنزيل الملف على هذا الجهاز.");
                    return;
                }
                String filename = (mimeType != null && mimeType.equals("application/zip"))
                        ? "mosaic-training-data.zip" : "mosaic-download.bin";
                DownloadManager.Request request = new DownloadManager.Request(Uri.parse(url));
                request.setTitle(filename);
                request.setDescription("تنزيل ملف من MOSAIC");
                request.setMimeType(mimeType == null ? "application/octet-stream" : mimeType);
                request.addRequestHeader("User-Agent", userAgent == null ? "MOSAIC" : userAgent);
                request.setNotificationVisibility(
                        DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED);
                request.setDestinationInExternalPublicDir(Environment.DIRECTORY_DOWNLOADS, filename);
                manager.enqueue(request);
            } catch (Exception error) {
                statusText.setText("تعذر تنزيل الملف: " + error.getMessage());
            }
        });

        webView.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                String host = request.getUrl().getHost();
                if ("127.0.0.1".equals(host) || "localhost".equals(host)) {
                    return false;
                }
                try {
                    startActivity(new Intent(Intent.ACTION_VIEW, request.getUrl()));
                } catch (Exception ignored) {
                    // Do not navigate the embedded app to an untrusted origin.
                }
                return true;
            }
        });

        statusPanel = new LinearLayout(this);
        statusPanel.setOrientation(LinearLayout.VERTICAL);
        statusPanel.setGravity(Gravity.CENTER);
        statusPanel.setPadding(32, 32, 32, 32);

        TextView logo = new TextView(this);
        logo.setText("MOSAIC");
        logo.setTextColor(0xFFE6EDF3);
        logo.setTextSize(28);
        logo.setGravity(Gravity.CENTER);
        logo.setLetterSpacing(0.08f);

        statusText = new TextView(this);
        statusText.setText("يتم تشغيل محرك MOSAIC...");
        statusText.setTextColor(0xFF9AA7B5);
        statusText.setTextSize(15);
        statusText.setGravity(Gravity.CENTER);
        statusText.setPadding(0, 18, 0, 18);

        progress = new ProgressBar(this);
        progress.setIndeterminate(true);

        statusPanel.addView(logo);
        statusPanel.addView(statusText);
        statusPanel.addView(progress);

        root.addView(webView, new FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.MATCH_PARENT,
                FrameLayout.LayoutParams.MATCH_PARENT));
        root.addView(statusPanel, new FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.MATCH_PARENT,
                FrameLayout.LayoutParams.MATCH_PARENT));

        setContentView(root);
    }

    private final class MosaicJsBridge {
        @JavascriptInterface
        public void speak(String text) {
            runOnUiThread(() -> {
                if (textToSpeech != null) {
                    textToSpeech.setLanguage(new Locale("ar"));
                    textToSpeech.speak(text, TextToSpeech.QUEUE_FLUSH, null, "mosaic-response");
                }
            });
        }

        @JavascriptInterface
        public void startSpeech() {
            runOnUiThread(() -> {
                if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
                    pendingSpeechStart = true;
                    requestPermissions(new String[]{Manifest.permission.RECORD_AUDIO}, REQUEST_AUDIO);
                } else {
                    beginNativeSpeech();
                }
            });
        }

        @JavascriptInterface
        public void stopSpeech() {
            runOnUiThread(() -> {
                if (speechRecognizer != null) speechRecognizer.stopListening();
            });
        }
    }

    private void beginNativeSpeech() {
        if (!SpeechRecognizer.isRecognitionAvailable(this)) {
            webView.evaluateJavascript("document.getElementById('vresult').textContent='خدمة التعرف على الكلام غير متاحة على هذا الجهاز.';", null);
            return;
        }
        if (speechRecognizer != null) speechRecognizer.destroy();
        speechRecognizer = SpeechRecognizer.createSpeechRecognizer(this);
        speechRecognizer.setRecognitionListener(new RecognitionListener() {
            @Override public void onReadyForSpeech(Bundle params) {
                webView.evaluateJavascript("document.getElementById('vresult').textContent='أستمع إليك الآن…';", null);
            }
            @Override public void onBeginningOfSpeech() {}
            @Override public void onRmsChanged(float rmsdB) {}
            @Override public void onBufferReceived(byte[] buffer) {}
            @Override public void onEndOfSpeech() {}
            @Override public void onError(int error) {
                webView.evaluateJavascript("document.getElementById('vresult').textContent='انتهى الاستماع أو تعذر فهم الكلام. حاول مجدداً.';", null);
            }
            @Override public void onResults(Bundle results) {
                ArrayList<String> matches = results.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION);
                if (matches != null && !matches.isEmpty()) {
                    String safe = JSONObject.quote(matches.get(0));
                    webView.evaluateJavascript("document.getElementById('vinput').value=" + safe + ";", null);
                }
            }
            @Override public void onPartialResults(Bundle partialResults) {
                ArrayList<String> matches = partialResults.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION);
                if (matches != null && !matches.isEmpty()) {
                    String safe = JSONObject.quote(matches.get(0));
                    webView.evaluateJavascript("document.getElementById('vinput').value=" + safe + ";", null);
                }
            }
            @Override public void onEvent(int eventType, Bundle params) {}
        });
        Intent intent = new Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH);
        intent.putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM);
        intent.putExtra(RecognizerIntent.EXTRA_LANGUAGE, "ar");
        intent.putExtra(RecognizerIntent.EXTRA_LANGUAGE_PREFERENCE, "ar");
        intent.putExtra(RecognizerIntent.EXTRA_PARTIAL_RESULTS, true);
        speechRecognizer.startListening(intent);
    }

    @Override
    public void onRequestPermissionsResult(int requestCode, String[] permissions, int[] grantResults) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults);
        if (requestCode == REQUEST_AUDIO) {
            boolean granted = grantResults.length > 0 && grantResults[0] == PackageManager.PERMISSION_GRANTED;
            if (granted && pendingSpeechStart) beginNativeSpeech();
            else webView.evaluateJavascript("document.getElementById('vresult').textContent='لم تمنح إذن الميكروفون.';", null);
            pendingSpeechStart = false;
        }
    }

    @Override
    protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        super.onActivityResult(requestCode, resultCode, data);
        if (requestCode == REQUEST_FILES && fileCallback != null) {
            Uri[] results = null;
            if (resultCode == RESULT_OK && data != null) {
                if (data.getClipData() != null) {
                    int count = data.getClipData().getItemCount();
                    results = new Uri[count];
                    for (int i = 0; i < count; i++) results[i] = data.getClipData().getItemAt(i).getUri();
                } else if (data.getData() != null) {
                    results = new Uri[]{data.getData()};
                }
            }
            fileCallback.onReceiveValue(results);
            fileCallback = null;
        }
    }

    private void startMosaic() {
        statusText.setText("تهيئة Python وقاعدة البيانات المحلية...");
        io.execute(() -> {
            try {
                if (!Python.isStarted()) {
                    Python.start(new AndroidPlatform(getApplicationContext()));
                }
                PyObject bridge = Python.getInstance().getModule("mosaic.android_bridge");
                String port = bridge.callAttr("start_server",
                        getFilesDir().getAbsolutePath()).toString();
                baseUrl = "http://127.0.0.1:" + port;

                runOnUiThread(() -> {
                    statusPanel.setVisibility(View.GONE);
                    progress.setVisibility(View.GONE);
                    webView.setVisibility(View.VISIBLE);
                    webView.loadUrl(baseUrl + "/");
                    publishDeviceState();
                });
            } catch (Throwable error) {
                String message = error.getClass().getSimpleName() + ": " + error.getMessage();
                runOnUiThread(() -> showStartupError(message));
            }
        });
    }

    private void showStartupError(String message) {
        progress.setVisibility(View.GONE);
        statusText.setText("تعذر تشغيل MOSAIC\n" + message);
        Button retry = new Button(this);
        retry.setText("إعادة المحاولة");
        retry.setOnClickListener(v -> {
            retry.setVisibility(View.GONE);
            progress.setVisibility(View.VISIBLE);
            statusText.setText("إعادة تهيئة محرك MOSAIC...");
            startMosaic();
        });

        View parent = (View) statusText.getParent();
        if (parent instanceof LinearLayout) {
            ((LinearLayout) parent).addView(retry);
        }
    }

    @Override
    protected void onResume() {
        super.onResume();
        if (baseUrl != null) {
            publishDeviceState();
        }
    }

    private void publishDeviceState() {
        if (baseUrl == null) return;

        Intent battery = registerReceiver(null,
                new IntentFilter(Intent.ACTION_BATTERY_CHANGED));
        int level = 100;
        boolean charging = false;
        if (battery != null) {
            int rawLevel = battery.getIntExtra(BatteryManager.EXTRA_LEVEL, -1);
            int scale = battery.getIntExtra(BatteryManager.EXTRA_SCALE, -1);
            if (rawLevel >= 0 && scale > 0) {
                level = Math.round(rawLevel * 100f / scale);
            }
            int state = battery.getIntExtra(BatteryManager.EXTRA_STATUS, -1);
            charging = state == BatteryManager.BATTERY_STATUS_CHARGING
                    || state == BatteryManager.BATTERY_STATUS_FULL;
        }

        boolean metered = true;
        try {
            ConnectivityManager cm = (ConnectivityManager)
                    getSystemService(Context.CONNECTIVITY_SERVICE);
            metered = cm == null || cm.isActiveNetworkMetered();
        } catch (Exception ignored) {
            // Treat unknown connectivity as metered to avoid unexpected data use.
        }

        boolean thermalOk = true;
        if (Build.VERSION.SDK_INT >= 29) {
            try {
                PowerManager pm = (PowerManager) getSystemService(Context.POWER_SERVICE);
                thermalOk = pm == null
                        || pm.getCurrentThermalStatus() < PowerManager.THERMAL_STATUS_SEVERE;
            } catch (Exception ignored) {
                thermalOk = true;
            }
        }

        final int batteryPct = level;
        final boolean isCharging = charging;
        final boolean isMetered = metered;
        final boolean isThermalOk = thermalOk;

        io.execute(() -> {
            HttpURLConnection connection = null;
            try {
                JSONObject payload = new JSONObject();
                payload.put("battery_pct", batteryPct);
                payload.put("charging", isCharging);
                payload.put("metered_network", isMetered);
                payload.put("thermal_ok", isThermalOk);

                connection = (HttpURLConnection) new URL(baseUrl + "/api/device").openConnection();
                connection.setRequestMethod("POST");
                connection.setConnectTimeout(3000);
                connection.setReadTimeout(3000);
                connection.setDoOutput(true);
                connection.setRequestProperty("Content-Type", "application/json; charset=utf-8");
                byte[] body = payload.toString().getBytes(StandardCharsets.UTF_8);
                connection.setFixedLengthStreamingMode(body.length);
                try (OutputStream output = connection.getOutputStream()) {
                    output.write(body);
                }
                connection.getResponseCode();
            } catch (Exception ignored) {
                // The app remains usable if a device-state update fails.
            } finally {
                if (connection != null) connection.disconnect();
            }
        });
    }

    @Override
    public void onBackPressed() {
        if (webView != null && webView.canGoBack()) {
            webView.goBack();
        } else {
            super.onBackPressed();
        }
    }

    @Override
    protected void onDestroy() {
        super.onDestroy();
        if (speechRecognizer != null) { speechRecognizer.destroy(); speechRecognizer = null; }
        if (textToSpeech != null) { textToSpeech.stop(); textToSpeech.shutdown(); textToSpeech = null; }
        if (fileCallback != null) { fileCallback.onReceiveValue(null); fileCallback = null; }
        if (webView != null) {
            webView.stopLoading();
            webView.destroy();
            webView = null;
        }
        io.shutdown();
    }
}
