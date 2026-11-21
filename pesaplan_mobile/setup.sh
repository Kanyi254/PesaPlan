#!/usr/bin/env bash
# Linux/macOS version of setup.ps1. Run once from this folder (the one containing pubspec.yaml):
#     bash setup.sh
set -euo pipefail
command -v flutter >/dev/null || { echo "Flutter is not on PATH. Follow step 1 in README_MOBILE.md first."; exit 1; }

# keep our files safe from the generator
rm -rf _keep && mkdir _keep
cp -r lib _keep/lib && cp pubspec.yaml _keep/pubspec.yaml && cp -r test _keep/test

flutter create --org com.pesaplan --project-name pesaplan --platforms android .

cp -r _keep/lib/. lib/
cp _keep/pubspec.yaml pubspec.yaml
rm -f test/widget_test.dart
cp -r _keep/test/. test/
rm -rf _keep

# Android side: the SMS receiver + bridge
KT=android/app/src/main/kotlin/com/pesaplan/pesaplan
mkdir -p "$KT"
cp android_files/kotlin/*.kt "$KT"/

python3 - <<'PY'
import re
p = "android/app/src/main/AndroidManifest.xml"
x = open(p, encoding="utf-8").read()
if "READ_SMS" in x:
    print("Manifest already patched.")
else:
    perms = ('<uses-permission android:name="android.permission.INTERNET"/>\n'
             '    <uses-permission android:name="android.permission.READ_SMS"/>\n'
             '    <uses-permission android:name="android.permission.RECEIVE_SMS"/>\n'
             '    <application')
    receiver = ('<receiver android:name=".SmsReceiver" android:exported="true" '
                'android:permission="android.permission.BROADCAST_SMS">\n'
                '            <intent-filter android:priority="999">\n'
                '                <action android:name="android.provider.Telephony.SMS_RECEIVED"/>\n'
                '            </intent-filter>\n'
                '        </receiver>\n'
                '    </application>')
    assert "<application" in x and "</application>" in x, "unexpected manifest layout"
    x = x.replace("<application", perms, 1).replace("</application>", receiver, 1)
    open(p, "w", encoding="utf-8").write(x)
    print("Manifest updated.")
PY

flutter pub get
echo
echo "Done. Next:  flutter test   (checks the SMS parser)   then   flutter build apk --release"
