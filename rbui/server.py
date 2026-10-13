"""Connecting to RomM, shared by Settings and the first-run Welcome: the server address, the
token, Verify TLS certificate, pairing with RomM's code and the connection check."""
import rommlib
from rbui import theme

TLS_OFF_WARNING = "Certificates aren't checked now — only for a self-signed RomM"

# one plain consequence per Verify TLS certificate choice (Welcome, Advanced)
TLS_SAYS = {"on": "Only certificates a public authority signed, as a browser checks. The usual choice.",
            "off": "Any certificate, even a self-signed one. Someone on your network could read your token.",
            "ca": "Checks stay on, against your own authority's certificate (verify_tls in config.json)."}


def tls_mode(value):
    """verify_tls as "on", "off" or "ca"."""
    v = rommlib.norm_verify(value)
    return "on" if v is True else "off" if v is False else "ca"


def set_verify(app, value):
    """Verify TLS certificate to value (True, False or a CA bundle path): saved, reconnected,
    and the warning when checks go off."""
    rommlib.set_verify(app.cfg, value)
    applied(app)


def step_verify(app, step):
    """Verify TLS certificate one choice left (-1) or right (+1), as Settings' row cycles it."""
    rommlib.step_verify(app.cfg, step)
    applied(app)


def applied(app):
    app.apply_config(reconnect=True)
    if app.cfg["verify_tls"] is False:
        app.toast(TLS_OFF_WARNING, theme.AMBER)


def save_url(app, v, say=True):
    """A typed server address: cleaned, saved and reconnected. False (and a toast) when it
    can't be used."""
    try:
        url = rommlib.clean_server_url(v)
    except rommlib.RommError as e:
        app.toast(str(e), theme.RED)
        return False
    app.cfg["url"] = url
    app.apply_config(reconnect=True)
    if say:
        app.toast("Server saved — reconnecting", theme.GREEN)
    return True


def save_token(app, v, say=True):
    app.cfg["token"] = (v or "").strip()
    app.apply_config(reconnect=True)
    if say:
        app.toast("Token saved — reconnecting", theme.GREEN)


def pair(app, code, done=None, failed=None):
    """Swap RomM's pairing code for a token (off the UI thread): stored, reconnected, then
    done(res) (default: a toast). failed(e) on any error (default: a red toast); a malformed
    code fails at once, without asking RomM. False when it didn't start."""
    failed = failed or (lambda e: app.toast(str(e), theme.RED))
    try:
        code = rommlib.normalize_pair_code(code)
    except rommlib.RommError as e:
        failed(e)
        return False

    def paired(res):
        app.cfg["token"] = res["token"]
        app.apply_config(reconnect=True)
        if done:
            done(res)
        else:
            app.toast("Paired with RomM" + (" (%s)" % res["name"] if res.get("name") else "")
                      + " — reconnecting", theme.GREEN)
    if not done:
        app.toast("Pairing…")
    url, verify = app.cfg["url"], app.cfg["verify_tls"]
    app.bg(lambda: rommlib.exchange_pair_code(url, code, verify), paired, failed)
    return True


def check_server(app, url, done, failed, token=None):
    """The connection check (RomM's platform list) against url with the current Verify TLS
    certificate and token (or the one given), off the UI thread: done(platforms, RomM's version or
    None), or failed(e)
    with e a RommError (AuthRejected: RomM is there but wants a token; TlsRejected: its
    certificate didn't verify)."""
    api = rommlib.Api(url, app.cfg["token"] if token is None else token, app.cfg["verify_tls"])
    app.bg(lambda: (api.platforms(), api.server_version()), lambda r: done(*r), failed)
