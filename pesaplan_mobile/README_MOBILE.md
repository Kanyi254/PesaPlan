# PesaPlan for Android (Pixel 7a)

Shows your accounts with opening/closing balances, lets you add and edit transactions and transfers, keeps budgets,
syncs with your PC(s) through your own server, and records M-Pesa SMS automatically.

Everything runs on your phone first (it works offline) and syncs when the server is reachable.

## 1. One-time: install Flutter and the Android tools (Manjaro)

    sudo pacman -S --needed git unzip curl jdk17-openjdk android-tools
    sudo archlinux-java set java-17-openjdk

Flutter: use the official download from https://docs.flutter.dev/install (Linux), unpack it, e.g.

    mkdir -p ~/development && cd ~/development
    git clone https://github.com/flutter/flutter.git -b stable
    echo 'export PATH="$HOME/development/flutter/bin:$PATH"' >> ~/.bashrc   # or ~/.zshrc if you use zsh
    source ~/.bashrc

(An AUR `flutter` package also exists; the official clone updates more reliably with `flutter upgrade`.)

Android SDK, the easy way: install Android Studio from the AUR (`yay -S android-studio`), open it once and let its
setup wizard download the SDK, then:

    flutter config --android-sdk ~/Android/Sdk      # the path the wizard used
    flutter doctor --android-licenses                # press y to each
    flutter doctor

Everything under "Android toolchain" should have a tick. (You do not need Chrome or Linux-desktop items.)

## 2. Create the project

Unzip this folder, open a terminal in it (the folder with `pubspec.yaml`) and run:

    bash setup.sh

It generates the Android project, adds the SMS receiver and permissions, and fetches packages.
(On Windows use `setup.ps1` instead.)

## 3. Check the SMS reader

    flutter test

These tests feed the phone's parser the same messages the PC's parser read, and expect identical results.
If any fail, send me the output.

## 4. Build and install

    flutter build apk --release

The file is `build/app/outputs/flutter-apk/app-release.apk`. Easiest: turn on Developer options and USB debugging on
the Pixel, plug it in, accept the prompt on the phone, then `adb install -r build/app/outputs/flutter-apk/app-release.apk`.
Or copy the file to the phone and open it. Android will ask to allow installs from that app (Files/Chrome):
allow it once. Play Protect may say "unknown developer": choose "Install anyway".

## 5. First run on the phone

1. Install the **Tailscale** app on the phone and sign in with the same account as the server.
2. Open PesaPlan, go to the **Sync** tab, enter your server address (`https://...ts.net`), your email and password,
   and tap **Sign in** (the first device ever uses **Create account**, but you already did that for the PC,
   so just sign in). Your accounts and history arrive within seconds.
3. In the same tab, tap **Allow SMS access** and say yes. If Android blocks the permission for a sideloaded app:
   long-press the PesaPlan icon, App info, the three dots, **Allow restricted settings**, then try again.
4. Pick which account M-Pesa messages go into (normally "M-Pesa"). Tap **Import now** to pull in this month's messages.
5. Settings, Apps, PesaPlan, Battery: choose **Unrestricted** so Android never stops it from catching messages.

## How it behaves

* A new M-Pesa SMS is saved within a few seconds even if the app is closed, then sent to the server.
  If anything stops that (no network, phone just rebooted), the message stays in the phone's inbox and is picked up
  the next time you open the app. Duplicates are skipped by M-Pesa code.
* Withdrawals and deposits become transfers between M-Pesa and Cash, like on the PC.
* Edits you make are sent about 5 seconds later; other devices' changes arrive every 5 minutes and whenever you open the app.
* Phone accounts are not renamed here; rename on the PC and it syncs. Tap an account to change its opening balance.

## If something doesn't build

Send me the full red error text. I could not compile the app in my own environment, so expect to trade one or two
rounds of error messages before the first successful build.
