"""One-time Schwab OAuth login. Run:  python auth.py

1. Opens (prints) the Schwab login URL.
2. You log in and approve; Schwab redirects to your callback URL
   (e.g. https://127.0.0.1/?code=...&session=...). The page will fail to load - that's fine.
3. Paste the full redirected URL here. Tokens are saved to tokens.json (chmod 600).

The access token lasts 30 min and is refreshed automatically. The refresh token
lasts 7 days; after that, run this script again.
"""
import webbrowser
from urllib.parse import parse_qs, unquote, urlsplit

import config
from schwab_client import SchwabClient


def main():
    client = SchwabClient(config.SCHWAB_APP_KEY, config.SCHWAB_APP_SECRET, config.TOKEN_FILE,
                          config.SCHWAB_CALLBACK_URL)
    url = client.authorize_url()
    print("Open this URL, log in to Schwab and approve access:\n\n  " + url + "\n")
    try:
        webbrowser.open(url)
    except Exception:
        pass
    redirected = input("Paste the full URL you were redirected to: ").strip()
    code = parse_qs(urlsplit(redirected).query).get("code", [None])[0]
    if not code:
        raise SystemExit("No ?code= found in that URL.")
    client.exchange_code(unquote(code))
    print(f"Saved tokens to {config.TOKEN_FILE}. Quick check...")
    q = client.quotes(["SPY"])
    print("SPY last:", q.get("SPY", {}).get("quote", {}).get("lastPrice"))


if __name__ == "__main__":
    main()
