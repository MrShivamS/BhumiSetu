package com.example.bhumisetu;   // keep the package line your project already has

import android.app.DownloadManager;
import android.content.ActivityNotFoundException;
import android.content.Intent;
import android.graphics.Color;
import android.net.Uri;
import android.os.Bundle;
import android.os.Environment;
import android.webkit.URLUtil;
import android.webkit.ValueCallback;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Toast;

import androidx.activity.OnBackPressedCallback;
import androidx.activity.result.ActivityResultLauncher;
import androidx.activity.result.contract.ActivityResultContracts;
import androidx.appcompat.app.AppCompatActivity;
import androidx.core.graphics.Insets;
import androidx.core.view.ViewCompat;
import androidx.core.view.WindowInsetsCompat;

/**
 * One activity, two apps. Gradle builds it twice ("citizen" and "registrar" flavors); the only
 * differences are BuildConfig.START_URL, BuildConfig.APP_ROLE and the app name / package id.
 */
public class MainActivity extends AppCompatActivity {

    private WebView webView;
    private ValueCallback<Uri[]> filePathCallback;

    // Lets the page's "Choose file" buttons work (sale-deed upload, Aadhaar front/back photos).
    private final ActivityResultLauncher<Intent> fileChooser = registerForActivityResult(
            new ActivityResultContracts.StartActivityForResult(), result -> {
                if (filePathCallback != null) {
                    filePathCallback.onReceiveValue(
                            WebChromeClient.FileChooserParams.parseResult(result.getResultCode(), result.getData()));
                    filePathCallback = null;
                }
            });

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);

        webView = new WebView(this);
        webView.setBackgroundColor(Color.parseColor("#EDE7D6"));
        setContentView(webView);

        // Keep the page out from under the status bar, navigation bar and the keyboard.
        ViewCompat.setOnApplyWindowInsetsListener(webView, (v, insets) -> {
            Insets bars = insets.getInsets(WindowInsetsCompat.Type.systemBars() | WindowInsetsCompat.Type.ime());
            v.setPadding(bars.left, bars.top, bars.right, bars.bottom);
            return WindowInsetsCompat.CONSUMED;
        });

        WebSettings settings = webView.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);
        // The server reads this marker to tell the two apps apart (registrar app: no sign-up, registrar logins only).
        settings.setUserAgentString(settings.getUserAgentString() + " BhumiSetuApp/1.0 role/" + BuildConfig.APP_ROLE);
        // The login is a token the website keeps in localStorage (DOM storage above), not a cookie.
        WebView.setWebContentsDebuggingEnabled(BuildConfig.DEBUG);

        // OPTIONAL, citizen app only: native "Continue with Google". See README-android.md, part 8.
        // if (!BuildConfig.WEB_CLIENT_ID.isEmpty() && "citizen".equals(BuildConfig.APP_ROLE)) {
        //     webView.addJavascriptInterface(new GoogleSignInBridge(this, webView), "BhumiSetuNative");
        // }

        // Your website (GitHub Pages) and your API (Railway) both stay inside the app.
        final String ownHost = Uri.parse(BuildConfig.START_URL).getHost();
        final String apiHost = Uri.parse(BuildConfig.API_URL).getHost();
        webView.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                Uri uri = request.getUrl();
                String host = uri.getHost();
                if (host != null && (host.equals(ownHost) || host.equals(apiHost))) return false;   // stay inside the app
                if ("about".equals(uri.getScheme()) || "data".equals(uri.getScheme())) return false;
                try {
                    startActivity(new Intent(Intent.ACTION_VIEW, uri));                  // anything else: phone's browser
                } catch (ActivityNotFoundException ignored) { }
                return true;
            }

            @Override
            public void onReceivedError(WebView view, WebResourceRequest request, WebResourceError error) {
                if (request.isForMainFrame()) showOfflinePage(view);
            }
        });

        webView.setWebChromeClient(new WebChromeClient() {
            @Override
            public boolean onShowFileChooser(WebView wv, ValueCallback<Uri[]> callback, FileChooserParams params) {
                if (filePathCallback != null) filePathCallback.onReceiveValue(null);
                filePathCallback = callback;
                try {
                    fileChooser.launch(params.createIntent());
                    return true;
                } catch (Exception e) {
                    filePathCallback = null;
                    return false;
                }
            }
        });

        // A WebView can't show PDFs ("View document"). Hand them to the Download Manager. The link the server
        // hands out is signed and short-lived, so no login cookie or header is needed.
        webView.setDownloadListener((url, userAgent, contentDisposition, mimeType, contentLength) -> {
            DownloadManager.Request req = new DownloadManager.Request(Uri.parse(url));
            req.addRequestHeader("User-Agent", userAgent);
            req.setMimeType(mimeType);
            req.setNotificationVisibility(DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED);
            req.setDestinationInExternalPublicDir(Environment.DIRECTORY_DOWNLOADS,
                    URLUtil.guessFileName(url, contentDisposition, mimeType));
            ((DownloadManager) getSystemService(DOWNLOAD_SERVICE)).enqueue(req);
            Toast.makeText(this, "Downloading...", Toast.LENGTH_SHORT).show();
        });

        getOnBackPressedDispatcher().addCallback(this, new OnBackPressedCallback(true) {
            @Override
            public void handleOnBackPressed() {
                if (webView.canGoBack()) {
                    webView.goBack();
                } else {
                    setEnabled(false);
                    getOnBackPressedDispatcher().onBackPressed();
                }
            }
        });

        if (savedInstanceState == null) {
            webView.loadUrl(BuildConfig.START_URL);               // <- your server's address, set in build.gradle
        } else {
            webView.restoreState(savedInstanceState);
        }
    }

    private void showOfflinePage(WebView view) {
        String html = "<html><head><meta name='viewport' content='width=device-width, initial-scale=1'></head>"
                + "<body style='font-family:sans-serif;padding:32px;background:#EDE7D6;color:#22271F'>"
                + "<h2>Can't reach BhumiSetu</h2><p>Check your internet connection and try again.</p>"
                + "<p><a href='" + BuildConfig.START_URL + "' style='color:#33513B'>Try again</a></p></body></html>";
        view.loadDataWithBaseURL(null, html, "text/html", "UTF-8", null);
    }

    @Override
    protected void onSaveInstanceState(Bundle outState) {
        super.onSaveInstanceState(outState);
        webView.saveState(outState);
    }

    @Override
    protected void onDestroy() {
        if (webView != null) webView.destroy();
        super.onDestroy();
    }
}
