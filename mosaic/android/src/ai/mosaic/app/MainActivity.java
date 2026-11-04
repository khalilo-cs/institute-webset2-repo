package ai.mosaic.app;

import android.app.Activity;
import android.content.ActivityNotFoundException;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.res.AssetManager;
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

import java.io.BufferedReader;
import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.io.RandomAccessFile;
import java.net.HttpURLConnection;
import java.net.URL;
import java.util.Map;

/**
 * MOSAIC for Android. The Python learning core is embedded in the APK:
 * CPython (libpython + stdlib), the extension modules and a tiny launcher are
 * native libraries, and the mosaic package ships as assets. On launch the
 * activity extracts the Python files once, starts the core as a local process
 * on 127.0.0.1 and shows its interface. No Termux and no network needed.
 */
public class MainActivity extends Activity {
    private static final String PREFS = "mosaic";
    private static final String KEY_URL = "server_url";
    private static final String KEY_MODE = "mode";            // "embedded" | "remote"
    private static final String KEY_EXTRACTED = "extracted_for";
    private static final int PORT = 8765;
    private static final String LOCAL = "http://127.0.0.1:" + PORT;
    private static final String SETUP = "file:///android_asset/setup.html";
    private static final String LOADING = "file:///android_asset/loading.html";
    private static final int PICK_FILES = 1;

    private WebView web;
    private ValueCallback<Uri[]> pendingFiles;
    private volatile Process core;
    private volatile String activeBase = LOCAL;

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
                if (url.startsWith("file:///android_asset/") || url.startsWith(activeBase)) return false;
                try {  // links to other sites open in the user's browser
                    startActivity(new Intent(Intent.ACTION_VIEW, Uri.parse(url)));
                } catch (ActivityNotFoundException ignored) { }
                return true;
            }

            @Override
            public void onReceivedError(WebView view, WebResourceRequest req, WebResourceError err) {
                if (req.isForMainFrame() && !req.getUrl().toString().startsWith("file:")) {
                    view.loadUrl(SETUP + "?error=" + (isEmbedded() ? "core" : "remote"));
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
        launch();
    }

    // ------------------------------------------------------------------ modes
    private SharedPreferences prefs() { return getSharedPreferences(PREFS, MODE_PRIVATE); }

    private boolean isEmbedded() { return !"remote".equals(prefs().getString(KEY_MODE, "embedded")); }

    private void launch() {
        if (isEmbedded()) {
            activeBase = LOCAL;
            web.loadUrl(LOADING);
            new Thread(new Runnable() {
                @Override
                public void run() { startEmbeddedCore(); }
            }, "mosaic-core").start();
        } else {
            activeBase = prefs().getString(KEY_URL, LOCAL);
            web.loadUrl(activeBase);
        }
    }

    private void show(final String url) {
        runOnUiThread(new Runnable() {
            @Override
            public void run() { web.loadUrl(url); }
        });
    }

    // ------------------------------------------------------------------ embedded core
    private void startEmbeddedCore() {
        try {
            if (ping()) { show(LOCAL); return; }      // already running (e.g. activity recreated)
            prepareFiles();
            File files = getFilesDir();
            String libDir = getApplicationInfo().nativeLibraryDir;
            ProcessBuilder pb = new ProcessBuilder(
                    libDir + "/libmosaicpy.so", "-S", "-u", "-X", "utf8",
                    new File(files, "app/boot.py").getAbsolutePath(),
                    "serve", "--db", new File(files, "mosaic.db").getAbsolutePath(),
                    "--host", "127.0.0.1", "--port", String.valueOf(PORT));
            Map<String, String> env = pb.environment();
            env.put("PYTHONHOME", new File(files, "python").getAbsolutePath());
            env.put("PYTHONPATH", new File(files, "app").getAbsolutePath());
            env.put("LD_LIBRARY_PATH", libDir);
            env.put("MOSAIC_NATIVE_LIB_DIR", libDir);
            env.put("HOME", files.getAbsolutePath());
            env.put("TMPDIR", getCacheDir().getAbsolutePath());
            env.put("SSL_CERT_DIR", "/system/etc/security/cacerts");
            env.put("PYTHONDONTWRITEBYTECODE", "1");
            pb.directory(files);
            pb.redirectErrorStream(true);
            core = pb.start();
            pumpLog(core.getInputStream(), new File(files, "core.log"));
            for (int i = 0; i < 120; i++) {               // up to ~30 s on a slow first start
                if (ping()) { show(LOCAL); return; }
                if (!alive(core)) break;
                Thread.sleep(250);
            }
            show(SETUP + "?error=core");
        } catch (Exception e) {
            appendLog("startup failed: " + e);
            show(SETUP + "?error=core");
        }
    }

    private static boolean alive(Process p) {
        try { p.exitValue(); return false; } catch (IllegalThreadStateException e) { return true; }
    }

    private boolean ping() {
        HttpURLConnection c = null;
        try {
            c = (HttpURLConnection) new URL(LOCAL + "/api/chat/status").openConnection();
            c.setConnectTimeout(400);
            c.setReadTimeout(800);
            return c.getResponseCode() == 200;
        } catch (IOException e) {
            return false;
        } finally {
            if (c != null) c.disconnect();
        }
    }

    /** Extract the stdlib archive and the mosaic package once per installed version. */
    private void prepareFiles() throws Exception {
        long installed = getPackageManager().getPackageInfo(getPackageName(), 0).lastUpdateTime;
        if (prefs().getLong(KEY_EXTRACTED, -1) == installed
                && new File(getFilesDir(), "app/boot.py").exists()) return;
        File app = new File(getFilesDir(), "app");
        deleteTree(app);
        File lib = new File(getFilesDir(), "python/lib");
        lib.mkdirs();
        copyAsset("python/python313.zip", new File(lib, "python313.zip"));
        copyAssetDir("app", app);
        prefs().edit().putLong(KEY_EXTRACTED, installed).apply();
    }

    private void copyAssetDir(String path, File dest) throws IOException {
        AssetManager am = getAssets();
        String[] children = am.list(path);
        if (children == null || children.length == 0) { copyAsset(path, dest); return; }
        dest.mkdirs();
        for (String c : children) copyAssetDir(path + "/" + c, new File(dest, c));
    }

    private void copyAsset(String path, File dest) throws IOException {
        dest.getParentFile().mkdirs();
        InputStream in = getAssets().open(path);
        OutputStream out = new FileOutputStream(dest);
        try {
            byte[] buf = new byte[64 * 1024];
            int n;
            while ((n = in.read(buf)) > 0) out.write(buf, 0, n);
        } finally {
            in.close();
            out.close();
        }
    }

    private static void deleteTree(File f) {
        File[] kids = f.listFiles();
        if (kids != null) for (File k : kids) deleteTree(k);
        f.delete();
    }

    private void pumpLog(final InputStream in, final File log) {
        new Thread(new Runnable() {
            @Override
            public void run() {
                try {
                    BufferedReader r = new BufferedReader(new InputStreamReader(in, "UTF-8"));
                    FileOutputStream out = new FileOutputStream(log, false);
                    String line;
                    while ((line = r.readLine()) != null) {
                        out.write((line + "\n").getBytes("UTF-8"));
                        out.flush();
                    }
                    out.close();
                } catch (IOException ignored) { }
            }
        }, "mosaic-log").start();
    }

    private void appendLog(String msg) {
        try {
            FileOutputStream out = new FileOutputStream(new File(getFilesDir(), "core.log"), true);
            out.write((msg + "\n").getBytes("UTF-8"));
            out.close();
        } catch (IOException ignored) { }
    }

    private String logTail() {
        File f = new File(getFilesDir(), "core.log");
        if (!f.exists()) return "";
        try {
            RandomAccessFile r = new RandomAccessFile(f, "r");
            long len = r.length(), start = Math.max(0, len - 4000);
            byte[] b = new byte[(int) (len - start)];
            r.seek(start);
            r.readFully(b);
            r.close();
            return new String(b, "UTF-8");
        } catch (IOException e) {
            return "";
        }
    }

    private void stopCore() {
        Process p = core;
        core = null;
        if (p != null) p.destroy();
    }

    @Override
    protected void onDestroy() {
        if (isFinishing()) stopCore();
        super.onDestroy();
    }

    // ------------------------------------------------------------------ files & navigation
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

    /** Small API for the bundled setup page. */
    private class Bridge {
        @JavascriptInterface
        public String getServerUrl() { return prefs().getString(KEY_URL, "http://192.168.1.20:8765"); }

        @JavascriptInterface
        public boolean isEmbedded() { return MainActivity.this.isEmbedded(); }

        @JavascriptInterface
        public String getLog() { return logTail(); }

        @JavascriptInterface
        public void useEmbedded() {
            prefs().edit().putString(KEY_MODE, "embedded").apply();
            runOnUiThread(new Runnable() {
                @Override
                public void run() { launch(); }
            });
        }

        @JavascriptInterface
        public void connect(final String url) {
            final String clean = url == null ? "" : url.trim().replaceAll("/+$", "");
            if (!(clean.startsWith("http://") || clean.startsWith("https://"))) return;
            prefs().edit().putString(KEY_URL, clean).putString(KEY_MODE, "remote").apply();
            stopCore();
            runOnUiThread(new Runnable() {
                @Override
                public void run() { launch(); }
            });
        }

        @JavascriptInterface
        public void openSetup() { show(SETUP); }
    }
}
