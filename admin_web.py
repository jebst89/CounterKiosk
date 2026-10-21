#!/usr/bin/env python3
"""Counter Kiosk admin web app.

A small Flask panel for an administrator on the LOCAL NETWORK to:

  * set the counter to a specific value (persisted to the same SQLite DB the
    kiosk uses, via storage.CounterStore),
  * reboot the Pi,
  * shut down the Pi.

Security model (see README "Admin web app" for the full writeup):
  * Password is never stored in plaintext — only a PBKDF2 hash in
    ADMIN_CREDENTIALS_FILE, created by set_admin_password.py.
  * Session cookie is signed with a random secret (also in that file),
    HttpOnly, SameSite=Strict, Secure (HTTPS only).
  * Per-IP login lockout after repeated failures.
  * CSRF token required on every state-changing POST.
  * Destructive actions (reboot/shutdown) require typing a confirmation word.
  * Runs as a NON-root user. reboot/shutdown are performed by calling two fixed,
    allow-listed commands through sudo (no shell, no user-supplied arguments).
  * Intended for a trusted LAN only. Do not expose to the internet.

This app is deliberately dependency-light to suit the Pi Zero W.
"""

from __future__ import annotations

import functools
import hmac
import json
import os
import secrets
import subprocess
import sys
import time
from datetime import timedelta

from flask import (
    Flask,
    abort,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from werkzeug.security import check_password_hash

import config
from storage import CounterStore

# Fixed commands for the privileged actions. These are passed to subprocess as
# a list (shell=False) so nothing from a request is ever interpreted by a shell.
# The matching sudoers allow-list (see sudoers.d snippet) permits exactly these.
REBOOT_CMD = ["sudo", "-n", "/sbin/reboot"]
SHUTDOWN_CMD = ["sudo", "-n", "/sbin/poweroff"]

# Confirmation words the admin must type for destructive actions.
CONFIRM_WORDS = {"reboot": "REBOOT", "shutdown": "SHUTDOWN"}


def _load_credentials() -> dict:
    """Load the credentials JSON (password hash + session secret).

    Fails loudly if missing/invalid — the app must not run without a configured
    password.
    """
    path = config.ADMIN_CREDENTIALS_FILE
    if not os.path.isfile(path):
        sys.exit(
            f"[admin] credentials file not found at {path!r}.\n"
            f"Run:  python set_admin_password.py\n"
            f"to create it before starting the admin app."
        )
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if not data.get("password_hash") or not data.get("secret_key"):
            raise ValueError("missing password_hash or secret_key")
        return data
    except Exception as exc:
        sys.exit(f"[admin] credentials file at {path!r} is invalid: {exc}")


# Simple in-memory per-IP failed-login tracking. Resets on restart, which is
# fine for brute-force mitigation on a single-process LAN app.
_failed: dict[str, list] = {}  # ip -> [count, locked_until_epoch]


def _is_locked(ip: str) -> float:
    """Return seconds remaining in lockout for ip, or 0 if not locked."""
    rec = _failed.get(ip)
    if not rec:
        return 0.0
    _count, locked_until = rec
    remaining = locked_until - time.monotonic()
    return remaining if remaining > 0 else 0.0


def _record_failure(ip: str) -> None:
    count, _ = _failed.get(ip, (0, 0.0))
    count += 1
    locked_until = 0.0
    if count >= config.ADMIN_MAX_FAILED_ATTEMPTS:
        locked_until = time.monotonic() + config.ADMIN_LOCKOUT_SECONDS
        count = 0  # reset the counter; the lockout window now governs
    _failed[ip] = [count, locked_until]


def _clear_failures(ip: str) -> None:
    _failed.pop(ip, None)


def create_app() -> Flask:
    creds = _load_credentials()
    app = Flask(__name__)

    app.config.update(
        SECRET_KEY=creds["secret_key"],
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Strict",
        # Cookie is only sent over HTTPS. The app is served with TLS; this stops
        # the session cookie from ever traversing plain HTTP.
        SESSION_COOKIE_SECURE=True,
        PERMANENT_SESSION_LIFETIME=timedelta(
            seconds=config.ADMIN_SESSION_LIFETIME_SECONDS
        ),
        MAX_CONTENT_LENGTH=16 * 1024,  # tiny forms only; reject large bodies
    )
    password_hash = creds["password_hash"]

    # --- CSRF -----------------------------------------------------------------

    def _issue_csrf() -> str:
        token = session.get("_csrf")
        if not token:
            token = secrets.token_urlsafe(32)
            session["_csrf"] = token
        return token

    def _check_csrf() -> None:
        sent = request.form.get("_csrf", "")
        expected = session.get("_csrf", "")
        if not expected or not hmac.compare_digest(sent, expected):
            abort(400, "Invalid or missing CSRF token.")

    @app.context_processor
    def _inject_csrf():
        return {"csrf_token": _issue_csrf()}

    # --- security headers -----------------------------------------------------

    @app.after_request
    def _security_headers(resp):
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Referrer-Policy"] = "no-referrer"
        # Strict CSP: only our own inline-free resources. No external anything.
        resp.headers["Content-Security-Policy"] = (
            "default-src 'self'; style-src 'self'; script-src 'self'; "
            "img-src 'self'; base-uri 'none'; form-action 'self'; "
            "frame-ancestors 'none'"
        )
        resp.headers["Cache-Control"] = "no-store"
        return resp

    # --- auth helpers ---------------------------------------------------------

    def login_required(view):
        @functools.wraps(view)
        def wrapped(*args, **kwargs):
            if not session.get("authenticated"):
                return redirect(url_for("login"))
            return view(*args, **kwargs)

        return wrapped

    def _client_ip() -> str:
        # LAN-only, no proxy: use the direct peer address. We deliberately do
        # NOT trust X-Forwarded-For (it is client-settable and there is no
        # trusted proxy in this deployment).
        return request.remote_addr or "unknown"

    # --- routes ---------------------------------------------------------------

    @app.route("/", methods=["GET"])
    @login_required
    def dashboard():
        with CounterStore(config.DB_PATH) as store:
            current = store.get_count()
        return render_template("dashboard.html", count=current)

    @app.route("/login", methods=["GET", "POST"])
    def login():
        ip = _client_ip()
        if request.method == "POST":
            locked = _is_locked(ip)
            if locked:
                flash(
                    f"Too many failed attempts. Try again in "
                    f"{int(locked) + 1} seconds.",
                    "error",
                )
                return render_template("login.html"), 429

            _check_csrf()
            password = request.form.get("password", "")
            # Constant-time-ish check via werkzeug; always runs even on empty
            # input so timing does not leak whether a field was present.
            if check_password_hash(password_hash, password):
                _clear_failures(ip)
                session.clear()
                session.permanent = True
                session["authenticated"] = True
                _issue_csrf()  # fresh token for the authenticated session
                return redirect(url_for("dashboard"))
            _record_failure(ip)
            flash("Incorrect password.", "error")
            return render_template("login.html"), 401
        return render_template("login.html")

    @app.route("/logout", methods=["POST"])
    @login_required
    def logout():
        _check_csrf()
        session.clear()
        return redirect(url_for("login"))

    @app.route("/set-count", methods=["POST"])
    @login_required
    def set_count():
        _check_csrf()
        raw = request.form.get("value", "").strip()
        # Validate strictly: non-negative integer within SQLite INTEGER range.
        if not raw.isdigit():
            flash("Enter a whole number (0 or greater).", "error")
            return redirect(url_for("dashboard"))
        value = int(raw)
        if value > 9_223_372_036_854_775_807:
            flash("Number is too large.", "error")
            return redirect(url_for("dashboard"))
        with CounterStore(config.DB_PATH) as store:
            store.set_count(value)
        flash(f"Counter set to {value}.", "success")
        return redirect(url_for("dashboard"))

    def _run_privileged(cmd) -> tuple:
        """Run a fixed allow-listed command (no shell). Returns (ok, message)."""
        try:
            subprocess.run(cmd, check=True, timeout=10)
            return True, ""
        except FileNotFoundError:
            return False, "Command not found on this system."
        except subprocess.CalledProcessError as exc:
            return False, f"Command failed (exit {exc.returncode})."
        except subprocess.TimeoutExpired:
            # reboot/poweroff normally return immediately; a timeout is odd but
            # the action may still proceed.
            return True, ""

    @app.route("/reboot", methods=["POST"])
    @login_required
    def reboot():
        _check_csrf()
        if request.form.get("confirm", "") != CONFIRM_WORDS["reboot"]:
            flash('Type "REBOOT" to confirm.', "error")
            return redirect(url_for("dashboard"))
        ok, msg = _run_privileged(REBOOT_CMD)
        if ok:
            return render_template("goodbye.html", action="rebooting")
        flash(f"Reboot failed: {msg}", "error")
        return redirect(url_for("dashboard"))

    @app.route("/shutdown", methods=["POST"])
    @login_required
    def shutdown():
        _check_csrf()
        if request.form.get("confirm", "") != CONFIRM_WORDS["shutdown"]:
            flash('Type "SHUTDOWN" to confirm.', "error")
            return redirect(url_for("dashboard"))
        ok, msg = _run_privileged(SHUTDOWN_CMD)
        if ok:
            return render_template("goodbye.html", action="shutting down")
        flash(f"Shutdown failed: {msg}", "error")
        return redirect(url_for("dashboard"))

    return app


def main() -> None:
    app = create_app()
    cert = config.ADMIN_TLS_CERT
    key = config.ADMIN_TLS_KEY
    if not (os.path.isfile(cert) and os.path.isfile(key)):
        sys.exit(
            f"[admin] TLS cert/key not found ({cert}, {key}).\n"
            f"Generate them per the README before starting the admin app."
        )
    # Serve over HTTPS with the self-signed cert. threaded=True lets the single
    # process handle the odd concurrent request without a heavy WSGI server.
    app.run(
        host=config.ADMIN_BIND_HOST,
        port=config.ADMIN_BIND_PORT,
        ssl_context=(cert, key),
        threaded=True,
        debug=False,
    )


if __name__ == "__main__":
    main()
