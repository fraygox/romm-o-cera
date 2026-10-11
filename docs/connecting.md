# Connecting to RomM

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
