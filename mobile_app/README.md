# Willy Mobile Controller (Flutter App)

Cross-platform mobile application for Android and iOS that connects to the **Willy Central Server Hub**, providing:
- **Live Device Discovery**: Automatically detects your Windows PC, showing real-time battery %, charging status, CPU load, RAM usage, and active window.
- **Full-Duplex Voice Call**: Real-time voice interaction with animated glowing orb visualizer and **Speech Barge-In** (interruption).
- **Remote PC Controls**: Quick action buttons (Open Chrome, VS Code, Lock PC, Mute, Volume, Sleep) and custom natural language command bar.

---

## Getting Started

### 1. Prerequisites
- [Flutter SDK](https://docs.flutter.dev/get-started/install) (v3.0.0 or higher)
- Android Studio / Xcode (or an Android device / emulator)

### 2. Configuration
Open [`lib/services/api_service.dart`](lib/services/api_service.dart) or tap the **Settings** icon in the app:
- **Android Emulator**: Uses `http://10.0.2.2:8000` by default.
- **Physical Mobile Device**: Change to your server's local Wi-Fi IP (e.g. `http://192.168.1.100:8000`) or VPS domain.
- **Token**: Default is `change-me-to-a-long-random-token`.

### 3. Run the App
```bash
# Get dependencies
flutter pub get

# Run on connected device or emulator
flutter run
```

### 4. Testing
```bash
flutter test
flutter analyze
```
