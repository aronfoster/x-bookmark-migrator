"""One-time desktop consent; run with uv run --with google-auth-oauthlib.

Uses Google's official helper, a loopback listener and PKCE. Never run in CI.
"""

import argparse
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow

from x_bookmark_migrator.core import PilotError, Store


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--client-file", type=Path, required=True)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--scope", choices=("drive.file", "drive"), default="drive.file")
    args = parser.parse_args()
    store = Store(args.state_dir)
    scope = "https://www.googleapis.com/auth/" + args.scope
    with store.locked():
        flow = InstalledAppFlow.from_client_secrets_file(
            args.client_file, scopes=[scope], autogenerate_code_verifier=True
        )
        grant = flow.run_local_server(host="127.0.0.1", port=0, prompt="consent")
        if not grant.refresh_token or not grant.has_scopes([scope]):
            raise PilotError("Consent did not grant offline access and the requested scope.")
        store.write(
            "drive-credentials.private.json",
            {
                "type": "authorized_user",
                "client_id": grant.client_id,
                "client_secret": grant.client_secret,
                "refresh_token": grant.refresh_token,
                "requested_scopes": [scope],
            },
        )
    print("Drive offline grant saved privately. Run archive-probe in the selected runtime.")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # OAuth exceptions may include codes and URLs. Do not echo them.
        raise SystemExit("Drive authorization failed; no token details printed.") from None
