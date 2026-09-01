"""Small wrapper around the operating-system credential store.

Secrets are addressed by a one-way hash of their logical identity, so account
names and profile names are not written into Credential Manager entry names.
"""

import hashlib

try:
    import keyring
except ImportError:  # Manual login and unsaved AI settings remain available.
    keyring = None


def _service(namespace):
    return f"THUFood.{namespace}"


def _account(identity):
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def get_secret(namespace, identity):
    if keyring is None or not identity:
        return None
    try:
        return keyring.get_password(_service(namespace), _account(identity))
    except Exception:
        return None


def set_secret(namespace, identity, secret):
    if keyring is None or not identity or not secret:
        return False
    try:
        keyring.set_password(_service(namespace), _account(identity), secret)
        return True
    except Exception:
        return False


def delete_secret(namespace, identity):
    if keyring is None or not identity:
        return False
    try:
        keyring.delete_password(_service(namespace), _account(identity))
        return True
    except Exception:
        return False
