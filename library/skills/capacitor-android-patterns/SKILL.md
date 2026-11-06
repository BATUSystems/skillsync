---
name: capacitor-android-patterns
description: Cross-project Android + Capacitor patterns (JDK/Gradle compatibility, mobile dev environment, CORS/domain setup, build/deploy flow).
user-invokable: true
---

# Capacitor Android Patterns (Cross-Project)

## Kiedy używać

Użyj tego skilla, gdy task dotyczy:

- `{frontend-app}/android/*`
- `capacitor.config.ts`
- skryptów `cap:*` i build/deploy APK
- debugowania webview, deep-linków, back buttona
- CORS/domain setup dla mobile login

> Pliki `references/org-*.md`, jeśli są, to rozszerzenia warstwy organizacji: przeczytaj te, które pasują do projektu.

## Zasady ogólne

1. **Nie zmieniaj logiki biznesowej backendu** dla samych prac mobilnych, chyba że blocker jest infrastrukturalny (np. CORS).
2. **Wymuszaj wspieraną wersję JDK** dla Gradle Android (`JAVA_HOME`, `ORG_GRADLE_JAVA_HOME`).
3. **Trzymaj spójne domeny dev** w całym środowisku (frontend/backend/mobile).
4. **Rozdziel API publiczne i tenantowe**, jeśli architektura wymaga dwóch stref endpointów.

## Minimalny flow uruchomienia

1. Backend: uruchom API z poprawnym hostowaniem dla urządzeń mobilnych
2. Frontend: uruchom build/dev-server zgodny z konfiguracją Capacitor
3. Android: uruchom build + `cap sync` + `cap run` z kompatybilnym JDK

## Wzorzec komendy Android (must-have)

```bash
export JAVA_HOME=/path/to/supported-jdk
export ORG_GRADLE_JAVA_HOME=$JAVA_HOME
export PATH=$JAVA_HOME/bin:$PATH
source ~/.nvm/nvm.sh
nvm use 22
npm run build:capacitor && npx cap sync android && npx cap run android
```

W środowiskach gdzie SDK nie jest wykryte automatycznie, rozszerz komendę o:

```bash
export ANDROID_SDK_ROOT=/path/to/android-sdk
export ANDROID_HOME=/path/to/android-sdk
export PATH=$JAVA_HOME/bin:$ANDROID_SDK_ROOT/platform-tools:$ANDROID_SDK_ROOT/cmdline-tools/latest/bin:$PATH
```

## Push setup (generic)

### Android app

- `google-services.json` musi być w `{frontend-app}/android/app/google-services.json`
- po dodaniu pliku wykonaj pełny build/sync/run aplikacji

### Backend runtime

- Utrzymuj konfigurację push lokalnie w `.env` (gitignored), nie tylko w jednorazowym `export`:

```dotenv
FCM_ENABLED=True
FCM_PROJECT_ID=your-project-id
FCM_CREDENTIALS_PATH=/absolute/path/to/firebase-service-account.json
```

- Po zmianie `.env` zawsze restartuj Django + Celery worker.

### Foreground vs Background behavior

- Background (app zminimalizowana): systemowy banner Android powinien się pojawić.
- Foreground (app aktywna): brak systemowego bannera jest normalny; obsługuj `pushNotificationReceived` i pokaż in-app snackbar/banner.

## Najczęstsze problemy

### 1) `Unsupported class file major version 69`

- Przyczyna: Java 25 dla Gradle/AGP.
- Fix: wymuś JDK21 jak wyżej.

### 2) `common.errors.network` na loginie

- Sprawdź CORS dla originu frontendowego używanego w trybie mobile/dev.
- Weryfikacja: DevTools/MCP, request `POST /api/auth/magic/start` nie może kończyć się `net::ERR_FAILED`.

### 3) Pusta lista klubów

- Sprawdź, czy frontend używa `publicApiUrl` dla endpointu public tenants.
- Sprawdź mapowania domen/tenantów zgodnie z architekturą projektu.

### 4) `Default FirebaseApp failed to initialize`

- Przyczyna: brak lub błędny `google-services.json`.
- Fix: poprawny plik w `android/app/` + redeploy APK.

### 5) Push `FAILED` z błędem `Brak biblioteki firebase-admin`

- Przyczyna: brak backendowego SDK Firebase Admin.
- Fix: zainstaluj `firebase-admin` w backendowym venv.

### 6) `ERR_SDK_NOT_FOUND`

- Przyczyna: native-run nie widzi Android SDK.
- Fix: ustaw `ANDROID_SDK_ROOT` i `ANDROID_HOME`.

## Checklist zmian

- `capacitor.config.ts` ma poprawne `allowNavigation` i ustawienia android dev
- konfiguracja środowiska mobile ma poprawne hosty dev
- task Android używa kompatybilnego JDK
- login działa na web i mobile
