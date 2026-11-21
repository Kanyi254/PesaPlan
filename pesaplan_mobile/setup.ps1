# Run once, in PowerShell, from this folder (the one containing pubspec.yaml):
#     powershell -ExecutionPolicy Bypass -File .\setup.ps1
# It asks Flutter to generate the Android project, then drops in PesaPlan's SMS code and permissions.
$ErrorActionPreference = "Stop"
if (-not (Get-Command flutter -ErrorAction SilentlyContinue)) {
    throw "Flutter is not installed or not on PATH. Follow step 1 in README_MOBILE.md first."
}

# keep our files safe from the generator
Remove-Item -Recurse -Force .\_keep -ErrorAction SilentlyContinue
New-Item -ItemType Directory .\_keep | Out-Null
Copy-Item .\lib .\_keep\lib -Recurse
Copy-Item .\pubspec.yaml .\_keep\pubspec.yaml
Copy-Item .\test .\_keep\test -Recurse

flutter create --org com.pesaplan --project-name pesaplan --platforms android .

Copy-Item .\_keep\lib\* .\lib\ -Recurse -Force
Copy-Item .\_keep\pubspec.yaml .\pubspec.yaml -Force
Remove-Item .\test\widget_test.dart -ErrorAction SilentlyContinue
Copy-Item .\_keep\test\* .\test\ -Recurse -Force
Remove-Item -Recurse -Force .\_keep

# Android side: the SMS receiver + bridge
$kt = "android\app\src\main\kotlin\com\pesaplan\pesaplan"
New-Item -ItemType Directory -Force $kt | Out-Null
Copy-Item .\android_files\kotlin\*.kt $kt -Force

$manifest = "android\app\src\main\AndroidManifest.xml"
$x = [System.IO.File]::ReadAllText((Resolve-Path $manifest))
if ($x -notmatch "READ_SMS") {
    $perms = @'
<uses-permission android:name="android.permission.INTERNET"/>
    <uses-permission android:name="android.permission.READ_SMS"/>
    <uses-permission android:name="android.permission.RECEIVE_SMS"/>
    <application
'@
    $receiver = @'
<receiver android:name=".SmsReceiver" android:exported="true" android:permission="android.permission.BROADCAST_SMS">
            <intent-filter android:priority="999">
                <action android:name="android.provider.Telephony.SMS_RECEIVED"/>
            </intent-filter>
        </receiver>
    </application>
'@
    $x = $x.Replace("<application", $perms.TrimEnd())
    $x = $x.Replace("</application>", $receiver)
    [System.IO.File]::WriteAllText((Resolve-Path $manifest), $x, (New-Object System.Text.UTF8Encoding $false))
    Write-Host "Manifest updated."
} else {
    Write-Host "Manifest already patched."
}

flutter pub get
Write-Host ""
Write-Host "Done. Next:  flutter test   (checks the SMS parser)   then   flutter build apk --release"
