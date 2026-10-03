plugins {
    id("com.android.application")
    id("kotlin-android")
    // The Flutter Gradle Plugin must be applied after the Android and Kotlin Gradle plugins.
    id("dev.flutter.flutter-gradle-plugin")
}

// Push notifications need the owner's Firebase file (git-ignored). Without it the app still
// builds and works; it just has no push.
if (file("google-services.json").exists()) {
    apply(plugin = "com.google.gms.google-services")
}

android {
    namespace = "com.example.willy_mobile"
    compileSdk = flutter.compileSdkVersion
    ndkVersion = flutter.ndkVersion

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_11
        targetCompatibility = JavaVersion.VERSION_11
    }

    kotlinOptions {
        jvmTarget = JavaVersion.VERSION_11.toString()
    }

    defaultConfig {
        // TODO: Specify your own unique Application ID (https://developer.android.com/studio/build/application-id.html).
        applicationId = "com.example.willy_mobile"
        // You can update the following values to match your application needs.
        // For more information, see: https://flutter.dev/to/review-gradle-config.
        minSdk = flutter.minSdkVersion
        targetSdk = flutter.targetSdkVersion
        versionCode = flutter.versionCode
        versionName = flutter.versionName
    }

    // Official releases are signed with the release key given to CI (WILLY_KEYSTORE_* variables);
    // local builds fall back to the debug key so `flutter run --release` keeps working.
    val releaseKeystore = System.getenv("WILLY_KEYSTORE_FILE")
    if (!releaseKeystore.isNullOrBlank()) {
        signingConfigs {
            create("release") {
                storeFile = file(releaseKeystore)
                storePassword = System.getenv("WILLY_KEYSTORE_PASSWORD")
                keyAlias = System.getenv("WILLY_KEY_ALIAS")
                keyPassword = System.getenv("WILLY_KEY_PASSWORD")
            }
        }
    }

    buildTypes {
        release {
            signingConfig = if (!releaseKeystore.isNullOrBlank()) signingConfigs.getByName("release")
                            else signingConfigs.getByName("debug")
        }
    }
}

flutter {
    source = "../.."
}
