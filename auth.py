"""Streamlit multi-user authentication.

Recommended deployment:
- Keep credentials in Streamlit Secrets (never in GitHub).
- Either provide ``password_hash`` containing a valid bcrypt hash, OR provide
  ``password`` as a private plaintext value in Streamlit Secrets.

Plaintext passwords in Streamlit Secrets are not committed to the repository;
Streamlit stores them as application secrets. bcrypt hashes remain the more
portable option when you prefer not to store the original password anywhere.
"""
from __future__ import annotations

import hmac
from typing import Any, Mapping, Tuple

import bcrypt
import streamlit as st


def _users():
    try:
        return st.secrets["auth"]["users"]
    except Exception:
        return {}


def _verify_password(password: str, rec: Mapping[str, Any]) -> Tuple[bool, str | None]:
    """Verify one user's password without allowing malformed secrets to crash.

    Supported secret formats:
        password_hash = "$2b$12$..."  # bcrypt, preferred
    or
        password = "my-private-password"  # Streamlit Secrets only
    """
    if not rec:
        return False, None

    # Preferred: bcrypt hash.
    raw_hash = rec.get("password_hash")
    if raw_hash is not None:
        hash_text = str(raw_hash).strip()

        # bcrypt hashes normally start with $2a$, $2b$, or $2y$ and are 60 chars.
        if not (hash_text.startswith(("$2a$", "$2b$", "$2y$")) and len(hash_text) == 60):
            return False, (
                "The password_hash configured for this user is not a valid bcrypt hash. "
                "Replace the placeholder hash, run setup_users.py, or use a private "
                "password = \"...\" entry in Streamlit Secrets."
            )

        try:
            ok = bcrypt.checkpw(password.encode("utf-8"), hash_text.encode("utf-8"))
            return bool(ok), None
        except (ValueError, TypeError) as exc:
            return False, f"Invalid bcrypt password_hash in Streamlit Secrets: {exc}"

    # Easier Streamlit Cloud option: plaintext value stored only in Secrets.
    raw_password = rec.get("password")
    if raw_password is not None:
        expected = str(raw_password)
        return hmac.compare_digest(password, expected), None

    return False, (
        "No password is configured for this user. Add either password_hash or password "
        "under the user's section in Streamlit Secrets."
    )


def logout():
    for k in ["auth_ok", "username", "role"]:
        st.session_state.pop(k, None)
    st.rerun()


def require_login() -> bool:
    if st.session_state.get("auth_ok"):
        return True

    st.title("🔐 Chilled Water Coil Designer")
    st.caption("Multi-user engineering access")
    users = _users()
    if not users:
        st.error(
            "No users are configured. Add [auth.users.admin], [auth.users.engineer1] "
            "and [auth.users.engineer2] to Streamlit Secrets."
        )
        st.stop()

    username = st.text_input("Username").strip()
    password = st.text_input("Password", type="password")

    if st.button("Login", type="primary"):
        rec = users.get(username)
        ok, config_error = _verify_password(password, rec) if rec else (False, None)

        if ok:
            st.session_state.auth_ok = True
            st.session_state.username = username
            st.session_state.role = str(rec.get("role", "engineer"))
            st.rerun()
        elif config_error:
            st.error(f"Login configuration error: {config_error}")
        else:
            st.error("Invalid username or password")

    st.stop()
    return False
