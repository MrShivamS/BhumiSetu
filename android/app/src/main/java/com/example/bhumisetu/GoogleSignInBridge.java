package com.example.bhumisetu;   // OPTIONAL file: only for "Continue with Google" in the citizen app

import android.os.CancellationSignal;
import android.webkit.JavascriptInterface;
import android.webkit.WebView;

import androidx.appcompat.app.AppCompatActivity;
import androidx.core.content.ContextCompat;
import androidx.credentials.Credential;
import androidx.credentials.CredentialManager;
import androidx.credentials.CredentialManagerCallback;
import androidx.credentials.CustomCredential;
import androidx.credentials.GetCredentialRequest;
import androidx.credentials.GetCredentialResponse;
import androidx.credentials.exceptions.GetCredentialCancellationException;
import androidx.credentials.exceptions.GetCredentialException;

import com.google.android.libraries.identity.googleid.GetGoogleIdOption;
import com.google.android.libraries.identity.googleid.GoogleIdTokenCredential;

import org.json.JSONObject;

/** The website calls BhumiSetuNative.googleSignIn(nonce); we show Android's Google account picker and
 *  hand the signed token back to the page, which sends it to the server for checking. */
public class GoogleSignInBridge {

    private final AppCompatActivity activity;
    private final WebView webView;

    public GoogleSignInBridge(AppCompatActivity activity, WebView webView) {
        this.activity = activity;
        this.webView = webView;
    }

    @JavascriptInterface
    public void googleSignIn(final String nonce) {
        activity.runOnUiThread(() -> start(nonce));
    }

    private void start(String nonce) {
        GetGoogleIdOption option = new GetGoogleIdOption.Builder()
                .setFilterByAuthorizedAccounts(false)           // also offer accounts that never used the app
                .setServerClientId(BuildConfig.WEB_CLIENT_ID)   // the WEB client id (same as GOOGLE_CLIENT_ID on the server)
                .setNonce(nonce)                                // the server checks it, so a stolen token can't be replayed
                .build();
        GetCredentialRequest request = new GetCredentialRequest.Builder().addCredentialOption(option).build();

        CredentialManager.create(activity).getCredentialAsync(
                activity, request, new CancellationSignal(), ContextCompat.getMainExecutor(activity),
                new CredentialManagerCallback<GetCredentialResponse, GetCredentialException>() {
                    @Override
                    public void onResult(GetCredentialResponse result) {
                        Credential credential = result.getCredential();
                        if (credential instanceof CustomCredential
                                && GoogleIdTokenCredential.TYPE_GOOGLE_ID_TOKEN_CREDENTIAL.equals(credential.getType())) {
                            try {
                                String idToken = GoogleIdTokenCredential
                                        .createFrom(((CustomCredential) credential).getData()).getIdToken();
                                run("window.onNativeGoogleToken(" + JSONObject.quote(idToken) + ")");
                            } catch (Exception e) {
                                fail("Google sign-in failed.");
                            }
                        } else {
                            fail("Unexpected sign-in result.");
                        }
                    }

                    @Override
                    public void onError(GetCredentialException e) {
                        fail(e instanceof GetCredentialCancellationException
                                ? "Sign-in was cancelled." : "Google sign-in failed.");
                    }
                });
    }

    private void run(String js) {
        webView.evaluateJavascript(js, null);
    }

    private void fail(String message) {
        run("window.onNativeGoogleError(" + JSONObject.quote(message) + ")");
    }
}
