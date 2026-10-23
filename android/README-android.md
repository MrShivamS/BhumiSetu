# Two Android apps (Citizen + Sub-Registrar)

Each app is a WebView that opens your BhumiSetu website on GitHub Pages. The website talks to your API on Railway.
Both apps are built from the same code with two Gradle "flavors": different app name, different package id (so both can be
installed on one phone) and different start page. The server recognises the app from its user agent: the Registrar app has no
sign-up and accepts only Sub-Registrar accounts; the Citizen app does not accept Sub-Registrar accounts. (That keeps each app tidy.
The real access control is still done on the server by account role.)

Not compiled by me (no Android SDK in my sandbox). The Java was syntax-checked only. If Android Studio shows an error, send me the message.

## 0. First put the website and the API online
You need two addresses before the apps can work:
- the website: `https://YOURNAME.github.io/YOUR-REPO/`  (GitHub Pages, see the main README)
- the API: `https://YOUR-APP.up.railway.app`  (Railway, see the main README)

## 1. Create the Android project
Android Studio > New Project > **Empty Views Activity** > Name `BhumiSetu` > Package name `com.example.bhumisetu` >
Language **Java** > Minimum SDK **API 24**. (If you choose another package name, change the first line of the .java files.)

## 2. Copy the files (from this folder, into your project)
| From this zip | To |
|---|---|
| `app/src/main/java/com/example/bhumisetu/MainActivity.java` | replace your `MainActivity.java` |
| `app/src/citizen/res/values/strings.xml` | create the folders `app/src/citizen/res/values/` and put the file there |
| `app/src/registrar/res/values/strings.xml` | same, in `app/src/registrar/res/values/` |

(Leave `GoogleSignInBridge.java` out for now. It is optional, part 8.) You can ignore `activity_main.xml`: it is no longer used.

## 3. AndroidManifest.xml
Add the permission above `<application>`, and two attributes on your `<activity>`:
```xml
<uses-permission android:name="android.permission.INTERNET" />

<application ...>
    <activity
        android:name=".MainActivity"
        android:exported="true"
        android:configChanges="orientation|screenSize|keyboardHidden|smallestScreenSize|screenLayout"
        android:windowSoftInputMode="adjustResize">
```
Do not add `usesCleartextTraffic` for the real apps: both addresses are `https://`.

## 4. app/build.gradle.kts: add the two flavors
Inside the existing `android { ... }` block (keep everything Android Studio generated):
```kotlin
    buildFeatures {
        buildConfig = true
    }

    flavorDimensions += "role"
    productFlavors {
        create("citizen") {
            dimension = "role"
            applicationId = "com.example.bhumisetu.citizen"
            buildConfigField("String", "START_URL", "\"https://YOURNAME.github.io/YOUR-REPO/\"")
            buildConfigField("String", "API_URL", "\"https://YOUR-APP.up.railway.app\"")
            buildConfigField("String", "APP_ROLE", "\"citizen\"")
            buildConfigField("String", "WEB_CLIENT_ID", "\"\"")
        }
        create("registrar") {
            dimension = "role"
            applicationId = "com.example.bhumisetu.registrar"
            buildConfigField("String", "START_URL", "\"https://YOURNAME.github.io/YOUR-REPO/login.html\"")
            buildConfigField("String", "API_URL", "\"https://YOUR-APP.up.railway.app\"")
            buildConfigField("String", "APP_ROLE", "\"registrar\"")
            buildConfigField("String", "WEB_CLIENT_ID", "\"\"")
        }
    }
```
If your file is `build.gradle` (Groovy) instead:
```groovy
    buildFeatures { buildConfig true }
    flavorDimensions "role"
    productFlavors {
        citizen {
            dimension "role"
            applicationId "com.example.bhumisetu.citizen"
            buildConfigField "String", "START_URL", "\"https://YOURNAME.github.io/YOUR-REPO/\""
            buildConfigField "String", "API_URL", "\"https://YOUR-APP.up.railway.app\""
            buildConfigField "String", "APP_ROLE", "\"citizen\""
            buildConfigField "String", "WEB_CLIENT_ID", "\"\""
        }
        registrar {
            dimension "role"
            applicationId "com.example.bhumisetu.registrar"
            buildConfigField "String", "START_URL", "\"https://YOURNAME.github.io/YOUR-REPO/login.html\""
            buildConfigField "String", "API_URL", "\"https://YOUR-APP.up.railway.app\""
            buildConfigField "String", "APP_ROLE", "\"registrar\""
            buildConfigField "String", "WEB_CLIENT_ID", "\"\""
        }
    }
```

## 5. Put your addresses in (this is the `loadUrl` step)
Replace the three placeholders in **both** flavors. The activity does `webView.loadUrl(BuildConfig.START_URL)`.
`START_URL` is the website, `API_URL` is the Railway API (the app keeps both hosts inside the app, so "View document" links work).
The website itself finds the API through `docs/js/config.js`, so edit that file too (main README, step 3).

## 6. Run it
Click **Sync Now**. Open **Build > Select Build Variant** and pick `citizenDebug` or `registrarDebug`, then press Run
(emulator or a phone with USB debugging).

## 7. Build the APK files
Quick test builds: **Build > Build APK(s)** for the selected variant, or in the Terminal:
```
gradlew assembleCitizenDebug assembleRegistrarDebug
```
Files appear in `app/build/outputs/apk/citizen/debug/` and `app/build/outputs/apk/registrar/debug/`.

Builds to hand out: **Build > Generate Signed App Bundle / APK > APK > Create new keystore**, then tick the `citizenRelease`
and `registrarRelease` variants. Keep the keystore file and its passwords safe and backed up: you need the same key for every future update.
To install a file on a phone, copy it over and allow "install unknown apps" for your file manager.

## 8. Optional: "Continue with Google" in the Citizen app
Google refuses its sign-in page inside a WebView, so without this step the Citizen app simply offers OTP login (the Google button stays hidden).
1. Add `GoogleSignInBridge.java` next to `MainActivity.java`.
2. In `app/build.gradle.kts` > `dependencies` (use the newest versions Android Studio offers):
   ```kotlin
   implementation("androidx.credentials:credentials:<version>")
   implementation("androidx.credentials:credentials-play-services-auth:<version>")
   implementation("com.google.android.libraries.identity.googleid:googleid:<version>")
   ```
3. In the `citizen` flavor set `WEB_CLIENT_ID` to your Google **Web application** client ID (the same value as `GOOGLE_CLIENT_ID` on the API).
4. In `MainActivity.java` remove the `//` in front of the four lines under "OPTIONAL, citizen app only".
5. Google Cloud Console: create an **Android** OAuth client with package `com.example.bhumisetu.citizen` and your signing key's SHA-1
   (`gradlew signingReport`; the debug and release keys have different SHA-1 values, so add both).

## Testing on your own computer first (emulator)
1. API: `cd backend && python app.py` (port 5000). Website: `cd docs && python -m http.server 8000`.
2. In `docs/js/config.js` set `window.BHUMISETU_API = "http://10.0.2.2:5000"` (10.0.2.2 is how the emulator reaches your computer).
3. Start the API with `ALLOWED_ORIGINS=http://10.0.2.2:8000` and in both flavors use `http://10.0.2.2:8000/` as `START_URL`
   and `http://10.0.2.2:5000` as `API_URL`.
4. Only for this test, add `android:usesCleartextTraffic="true"` to `<application>` (plain http). Remove it for the real apps.

## Troubleshooting
- **"Cannot find symbol BuildConfig"**: `buildConfig = true` is missing, or you did not press Sync.
- **"Can't reach BhumiSetu"** (the red/offline page inside the app): wrong `START_URL`, no internet, or GitHub Pages is not published yet. Open the same address in the phone's Chrome.
- **The site opens but says it can't reach the server**: wrong address in `docs/js/config.js`, the Railway service is down, or `ALLOWED_ORIGINS` on Railway does not match your site (it must be `https://YOURNAME.github.io`, no path).
- **Choose file does nothing**: you are not using this `MainActivity.java` (the file chooser lives there).
- **"View document" downloads a PDF instead of showing it**: expected, a WebView cannot display PDFs.
- **Citizen sees "This app is for Sub-Registrars"** (or the reverse): the account is in the wrong app; use the other APK.
- **Keyboard hides the field you type in**: tell me your Android version and I will adjust the window-insets code.
- **Logged out after a while**: a login lasts 7 days of use (up to 30 days in total). Also check that Railway's `SECRET_KEY` and disk do not change on every deploy.
