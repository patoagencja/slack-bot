#!/usr/bin/env python3
"""
Generuje nowy GOOGLE_ADS_REFRESH_TOKEN.

1. pip install google-auth-oauthlib
2. python refresh_token.py
3. Przeglądarka się otworzy → kliknij Zezwól
4. Skopiuj token i wklej na Render
"""
import os, sys

try:
    from google_auth_oauthlib.flow import InstalledAppFlow
except ImportError:
    print("Brak biblioteki. Uruchom:\n  pip install google-auth-oauthlib\n")
    sys.exit(1)

CLIENT_ID     = os.getenv("GOOGLE_ADS_CLIENT_ID")     or input("CLIENT_ID:     ").strip()
CLIENT_SECRET = os.getenv("GOOGLE_ADS_CLIENT_SECRET") or input("CLIENT_SECRET: ").strip()

flow = InstalledAppFlow.from_client_config(
    {"installed": {
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
        "redirect_uris": ["urn:ietf:wg:oauth:2.0:oob", "http://localhost"],
    }},
    scopes=["https://www.googleapis.com/auth/adwords"],
)

print("\nOtwieram przeglądarkę — zaloguj się i kliknij Zezwól...\n")
creds = flow.run_local_server(port=0)

print("\n" + "=" * 60)
print("✅  Wklej to na Render jako GOOGLE_ADS_REFRESH_TOKEN:")
print("=" * 60)
print(creds.refresh_token)
print("=" * 60)
