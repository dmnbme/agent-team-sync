"""HTTPS context. The python.org macOS installer ships without CA certificates until the user runs
'Install Certificates.command', so every HTTPS call fails with CERTIFICATE_VERIFY_FAILED. Fall back to certifi, then
to the system bundle."""
import os
import ssl

_CTX = None


def ssl_context():
    global _CTX
    if _CTX is None:
        ctx = ssl.create_default_context()
        if not ctx.cert_store_stats().get('x509_ca'):
            try:
                import certifi
                ctx.load_verify_locations(certifi.where())
            except Exception:
                for f in ('/etc/ssl/cert.pem', '/etc/ssl/certs/ca-certificates.crt'):
                    if os.path.isfile(f):
                        ctx.load_verify_locations(f)
                        break
        _CTX = ctx
    return _CTX
