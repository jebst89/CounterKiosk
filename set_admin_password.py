#!/usr/bin/env python3
"""Create or update the admin web app credentials.

Writes ADMIN_CREDENTIALS_FILE (from config.py) containing:
  * password_hash : a PBKDF2-SHA256 hash of the chosen password (never the
                    plaintext),
  * secret_key    : a random key used to sign session cookies.

The file is written with 0600 permissions (owner read/write only) so other
users on the Pi cannot read the hash or the session secret.

Run interactively:

    python set_admin_password.py

The password is typed without echo and must be entered twice. A minimum length
is enforced; longer passphrases are strongly encouraged.
"""

from __future__ import annotations

import getpass
import json
import os
import secrets
import stat
import sys

from werkzeug.security import generate_password_hash

import config

MIN_LENGTH = 12


def _prompt_password() -> str:
    while True:
        pw = getpass.getpass("New admin password (min %d chars): " % MIN_LENGTH)
        if len(pw) < MIN_LENGTH:
            print(f"  Too short — use at least {MIN_LENGTH} characters.\n")
            continue
        confirm = getpass.getpass("Re-enter password: ")
        if pw != confirm:
            print("  Passwords did not match. Try again.\n")
            continue
        return pw


def main() -> None:
    path = config.ADMIN_CREDENTIALS_FILE

    if os.path.exists(path):
        resp = input(f"{path} already exists. Overwrite? [y/N] ").strip().lower()
        if resp != "y":
            print("Aborted; credentials unchanged.")
            return

    # Not a TTY (e.g. piped) — refuse, so a password is never read from a file
    # or left in shell history by accident.
    if not sys.stdin.isatty():
        sys.exit("Refusing to read a password from a non-interactive stdin.")

    password = _prompt_password()

    data = {
        # PBKDF2-SHA256 with a per-hash random salt and many iterations.
        "password_hash": generate_password_hash(
            password, method="pbkdf2:sha256:600000"
        ),
        # 256-bit random key for signing session cookies.
        "secret_key": secrets.token_urlsafe(48),
    }

    # Write atomically with restrictive permissions: create the file 0600 via a
    # temp file in the same directory, then rename into place.
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, stat.S_IRUSR | stat.S_IWUSR)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
        # Re-assert 0600 in case the umask affected the rename target.
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)

    print(f"\nSaved credentials to {path} (permissions 0600).")
    print("Restart the admin service for the new password to take effect:")
    print("  sudo systemctl restart counter-kiosk-admin.service")


if __name__ == "__main__":
    main()
