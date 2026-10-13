# Connecting to RomM

## The first start: Welcome

A box without a server address or a token opens on **Welcome**: three steps on
the left (Server address, Pair with RomM, Pick a platform), the current one on
the right.

1. **Server address**: type your RomM's address (`https://` is already there)
   and press Start (**Connect**). The app checks the server right away. **Y,
   Advanced** sets *Verify TLS certificate* (On, Off, or the CA bundle named in
   `config.json`, the same setting as in Settings) and has *Type an API token
   instead* for a RomM that can't pair. If the certificate doesn't verify,
   Welcome says which host, why, and offers Advanced, Try again or Edit the
   address.
2. **Pair with RomM**: type the 8-character code from RomM's **Pair** button.
   It checks itself as soon as the eighth character is in.
3. **Pick a platform**: the platform list opens once your library has loaded.

**B, Later** goes to Home and Welcome doesn't open by itself again
(`welcome_seen` in `config.json`); everything is in Settings too. Welcome also
opens when RomM turns the saved token away on the first platform fetch (a
revoked token), at the pairing step. A box whose `ROMM_URL` and `ROMM_TOKEN`
come from the environment never sees it.

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
