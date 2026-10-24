"""Hand the system proxy to git. git (curl) reads only http.proxy and the *_PROXY variables, never the OS proxy setting
that apps like Clash switch on; hooks started from a GUI app and the autosync scheduler have no shell variables either.
urllib reads the OS setting (macOS network settings, the Windows registry), so when no proxy variable is set, export
what it finds for git and gh. A git http.proxy the person configured still wins. PAC scripts are not read."""
import os
import urllib.request

KEYS = ('HTTPS_PROXY', 'https_proxy', 'HTTP_PROXY', 'http_proxy', 'ALL_PROXY', 'all_proxy')


def system_proxy_env(env=None, getproxies=urllib.request.getproxies):
    """The variables to add (empty when a proxy variable is already set or the system has none)."""
    env = os.environ if env is None else env
    if any(env.get(k) for k in KEYS):
        return {}
    try:
        p = getproxies()
    except Exception:  # noqa: BLE001
        return {}
    https, http = p.get('https') or p.get('http'), p.get('http') or p.get('https')
    if not https:
        return {}
    add = {'HTTPS_PROXY': https, 'HTTP_PROXY': http}
    if not (env.get('NO_PROXY') or env.get('no_proxy')):
        add['NO_PROXY'] = 'localhost,127.0.0.1,::1'
    return add


def apply():
    os.environ.update(system_proxy_env())
