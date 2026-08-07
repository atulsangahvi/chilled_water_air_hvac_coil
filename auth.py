"""Streamlit multi-user authentication using bcrypt hashes stored in st.secrets."""
import bcrypt
import streamlit as st


def _users():
    try:
        return st.secrets["auth"]["users"]
    except Exception:
        return {}


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
        st.error("No users are configured. Copy .streamlit/secrets.toml.example to .streamlit/secrets.toml and add bcrypt password hashes.")
        st.stop()

    username = st.text_input("Username")
    password = st.text_input("Password", type="password")
    if st.button("Login", type="primary"):
        rec = users.get(username)
        if rec and bcrypt.checkpw(password.encode("utf-8"), str(rec["password_hash"]).encode("utf-8")):
            st.session_state.auth_ok = True
            st.session_state.username = username
            st.session_state.role = str(rec.get("role", "engineer"))
            st.rerun()
        else:
            st.error("Invalid username or password")
    st.stop()
    return False
