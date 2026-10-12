# Connecting to RomM

## Signing in: pair with a code

Typing a 68-character token on a controller is no fun, so RomM 5 can hand it
over with a short code instead:

1. In the app: **Settings → Server address**, enter your RomM's address.
2. In RomM's web page: **Profile → Client API tokens**, create a token for this
   box (scopes in the [README](../README.md#1-make-a-romm-token)) or pick an
   existing one, and press **Pair**. RomM shows an 8-character code.
3. In the app: **Settings → Pair with RomM**, type the code (upper or lower
   case, dashes and spaces are ignored) and press **Save** (or Start). The
   code works once and only for about a minute, so have the box ready first.

The app swaps the code for the token (`POST /api/client-tokens/exchange`,
which needs no login), saves it and reconnects. A wrong, used or expired code
says so and leaves the current token alone: make a new code in RomM. RomM
allows a handful of tries a minute.

Pairing gives the token a **new secret**: anything else still using that
token stops working. Give each box its own token.

Typing the token under **Settings → API token** still works, as does
`ROMM_TOKEN` with `deploy.sh`.

## Network and certificates

- **HTTPS** (recommended): certificates are verified by default.
- **Self-signed / private CA**: set `verify_tls` to the CA bundle's path on the
  box (best), or to `false` (encrypts, but doesn't check who you're talking to).
- **Plain HTTP** works — use an `http://` URL. The token then crosses the
  network unencrypted, which is fine on a trusted home LAN; Settings shows a
  warning so it's a conscious choice.
- **Batocera's SMB share** (`\\BATOCERA\share`, guest access by default)
  exposes all of `/userdata` — including `config.json` and this app's code —
  to anyone on your LAN. That's Batocera's design, not this app's; if your LAN
  isn't trusted, set a share password / disable the share, and keep the token
  read-only (it is, with the scopes in the [README](../README.md#1-make-a-romm-token))
  so a leak can't change your RomM.
- `user:password@` in the URL is refused: the token is the login.
- Behind an auth proxy (Authelia/Authentik forward-auth…)? The RomM API paths
  must be reachable with just the bearer token; the app reports "Not a RomM
  API response" otherwise.
