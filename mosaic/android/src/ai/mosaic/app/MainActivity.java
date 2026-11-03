package ai.mosaic.app;

import android.app.Activity;
import android.content.ActivityNotFoundException;
import android.content.Intent;
import android.content.SharedPreferences;
import android.graphics.Color;
import android.net.Uri;
import android.os.Bundle;
import android.webkit.JavascriptInterface;
import android.webkit.ValueCallback;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;

/**
 * Native shell for MOSAIC. The learning core (Python) runs as a local server —
 * on this phone through Termux, or on a computer in the same network — and this
 * activity shows its interface full-screen. A bundled setup screen explains how
 * to start the core and stores the server address.
 */
public class MainActivity extends Activity {
    private static final String PREFS = "mosaic";
    private static final String KEY_URL = "server_url";
    private static final String DEFAULT_URL = "http://127.0.0.1:8765";
    private static final String SETUP = "file:///android_asset/setup.html";
    private static final int PICK_FILES = 1;

    private WebView web;
    private ValueCallback<Uri[]> pendingFiles;

    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);
        web = new WebView(this);
        web.setBackgroundColor(Color.parseColor("#0e1526"));
        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setMediaPlaybackRequiresUserGesture(true);
        s.setAllowFileAccess(false);          // only android_asset pages, never device files
        s.setAllowContentAccess(false);
        web.addJavascriptInterface(new Bridge(), "MosaicApp");
        web.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView view, String url) {
                Uri target = Uri.parse(url);
                String server = serverUrl();
                if (url.startsWith("file:///android_asset/") || (server != null && url.startsWith(server))) {
                    return false;
                }
                try {  // links to other sites open in the user's browser
                    startActivity(new Intent(Intent.ACTION_VIEW, target));
                } catch (ActivityNotFoundException ignored) { }
                return true;
            }

            @Override
            public void onReceivedError(WebView view, WebResourceRequest req, WebResourceError err) {
                if (req.isForMainFrame()) {
                    view.loadUrl(SETUP + "?error=1");
                }
            }
        });
        web.setWebChromeClient(new WebChromeClient() {
            @Override
            public boolean onShowFileChooser(WebView view, ValueCallback<Uri[]> callback, FileChooserParams params) {
                if (pendingFiles != null) pendingFiles.onReceiveValue(null);
                pendingFiles = callback;
                Intent pick = new Intent(Intent.ACTION_GET_CONTENT);
                pick.addCategory(Intent.CATEGORY_OPENABLE);
                pick.setType("*/*");
                pick.putExtra(Intent.EXTRA_ALLOW_MULTIPLE, true);
                try {
                    startActivityForResult(Intent.createChooser(pick, "اختر ملفًا"), PICK_FILES);
                } catch (ActivityNotFoundException e) {
                    pendingFiles = null;
                    return false;
                }
                return true;
            }
        });
        setContentView(web);
        String saved = serverUrl();
        web.loadUrl(saved == null ? SETUP : saved);
    }

    private String serverUrl() {
        return getSharedPreferences(PREFS, MODE_PRIVATE).getString(KEY_URL, null);
    }

    @Override
    protected void onActivityResult(int request, int result, Intent data) {
        if (request != PICK_FILES || pendingFiles == null) return;
        Uri[] uris = null;
        if (result == RESULT_OK && data != null) {
            if (data.getClipData() != null) {
                int n = data.getClipData().getItemCount();
                uris = new Uri[n];
                for (int i = 0; i < n; i++) uris[i] = data.getClipData().getItemAt(i).getUri();
            } else if (data.getData() != null) {
                uris = new Uri[]{data.getData()};
            }
        }
        pendingFiles.onReceiveValue(uris);
        pendingFiles = null;
    }

    @Override
    public void onBackPressed() {
        if (web.canGoBack()) web.goBack(); else super.onBackPressed();
    }

    /** Small, harmless API for the bundled setup page: read/save the server address. */
    private class Bridge {
        @JavascriptInterface
        public String getServerUrl() {
            String u = serverUrl();
            return u == null ? DEFAULT_URL : u;
        }

        @JavascriptInterface
        public void connect(final String url) {
            final String clean = url == null ? "" : url.trim().replaceAll("/+$", "");
            if (!(clean.startsWith("http://") || clean.startsWith("https://"))) return;
            SharedPreferences.Editor e = getSharedPreferences(PREFS, MODE_PRIVATE).edit();
            e.putString(KEY_URL, clean).apply();
            runOnUiThread(new Runnable() {
                @Override
                public void run() { web.loadUrl(clean); }
            });
        }

        @JavascriptInterface
        public void openSetup() {
            runOnUiThread(new Runnable() {
                @Override
                public void run() { web.loadUrl(SETUP); }
            });
        }
    }
}
