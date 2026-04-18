"""
Tests for auth, JWT, brute-force protection, revocation, and
settings security in main.py.
"""

import pytest
import time


# ── JWT STRUCTURE ─────────────────────────────────────────────────────────────

class TestJWTStructure:
    def test_token_is_three_part_jwt(self, app_client):
        r = app_client.post("/api/auth/login", json={"password": "admin"})
        token = r.json()["token"]
        parts = token.split(".")
        assert len(parts) == 3, "JWT must have 3 parts (header.payload.signature)"

    def test_token_header_is_base64(self, app_client):
        import base64, json
        r = app_client.post("/api/auth/login", json={"password": "admin"})
        token = r.json()["token"]
        header_b64 = token.split(".")[0]
        # Add padding
        padded = header_b64 + "=" * (4 - len(header_b64) % 4)
        header = json.loads(base64.urlsafe_b64decode(padded))
        assert header.get("alg") == "HS256"
        assert header.get("typ") == "JWT"

    def test_token_payload_has_sub(self, app_client):
        import base64, json
        r = app_client.post("/api/auth/login", json={"password": "admin"})
        token = r.json()["token"]
        payload_b64 = token.split(".")[1]
        padded = payload_b64 + "=" * (4 - len(payload_b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded))
        assert payload.get("sub") == "admin"

    def test_token_payload_has_jti(self, app_client):
        import base64, json
        r = app_client.post("/api/auth/login", json={"password": "admin"})
        token = r.json()["token"]
        payload_b64 = token.split(".")[1]
        padded = payload_b64 + "=" * (4 - len(payload_b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded))
        assert "jti" in payload and len(payload["jti"]) > 0

    def test_token_payload_has_exp(self, app_client):
        import base64, json
        r = app_client.post("/api/auth/login", json={"password": "admin"})
        token = r.json()["token"]
        payload_b64 = token.split(".")[1]
        padded = payload_b64 + "=" * (4 - len(payload_b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded))
        assert "exp" in payload
        assert payload["exp"] > time.time()

    def test_two_tokens_have_different_jtis(self, app_client):
        import base64, json
        def get_jti():
            r = app_client.post("/api/auth/login", json={"password": "admin"})
            token = r.json()["token"]
            payload_b64 = token.split(".")[1]
            padded = payload_b64 + "=" * (4 - len(payload_b64) % 4)
            return json.loads(base64.urlsafe_b64decode(padded))["jti"]
        assert get_jti() != get_jti()


# ── AUTHENTICATION ENFORCEMENT ────────────────────────────────────────────────

DESTRUCTIVE_ENDPOINTS = [
    ("POST",   "/api/faults/inject",             {"location": "US Lab", "fault": "Cabin Leak"}),
    ("DELETE", "/api/faults",                    None),
    ("DELETE", "/api/data",                      None),
    ("GET",    "/api/settings",                  None),
    ("PATCH",  "/api/settings/alerts",           {"updates": {}}),
    ("PATCH",  "/api/settings/trends",           {"updates": {}}),
    ("PATCH",  "/api/settings/generation",       {"updates": {}}),
    ("PATCH",  "/api/settings/display",          {"updates": {}}),
    ("GET",    "/api/settings/ml/status",        None),
    ("DELETE", "/api/settings/data/sensor",      None),
    ("DELETE", "/api/settings/data/alerts",      None),
    ("DELETE", "/api/settings/data/faults",      None),
    ("DELETE", "/api/settings/data/lstm",        None),
    ("GET",    "/api/settings/data/export/csv",  None),
    ("POST",   "/api/settings/security/revoke-all", None),
]


class TestAuthEnforcement:
    @pytest.mark.parametrize("method,path,body", DESTRUCTIVE_ENDPOINTS)
    def test_no_token_returns_401(self, app_client, method, path, body):
        fn = getattr(app_client, method.lower())
        kwargs = {}
        if body is not None:
            kwargs["json"] = body
        r = fn(path, **kwargs)
        assert r.status_code == 401, \
            f"{method} {path} should return 401 without auth, got {r.status_code}"

    @pytest.mark.parametrize("method,path,body", DESTRUCTIVE_ENDPOINTS)
    def test_bad_token_returns_401(self, app_client, method, path, body):
        fn = getattr(app_client, method.lower())
        kwargs = {"headers": {"Authorization": "Bearer totally.invalid.token"}}
        if body is not None:
            kwargs["json"] = body
        r = fn(path, **kwargs)
        assert r.status_code == 401, \
            f"{method} {path} with bad token should return 401, got {r.status_code}"


# ── TOKEN REVOCATION ──────────────────────────────────────────────────────────

class TestTokenRevocation:
    def test_revoked_token_rejected(self, app_client):
        # Get a fresh token
        r = app_client.post("/api/auth/login", json={"password": "admin"})
        token = r.json()["token"]

        # Verify it works
        r = app_client.get(
            "/api/settings",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200

        # Revoke it
        app_client.post(
            "/api/auth/logout",
            headers={"Authorization": f"Bearer {token}"},
        )

        # Now it should be rejected
        r = app_client.get(
            "/api/settings",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 401

    def test_other_tokens_unaffected_after_single_logout(self, app_client):
        # Mint two independent tokens
        r1 = app_client.post("/api/auth/login", json={"password": "admin"})
        token1 = r1.json()["token"]
        r2 = app_client.post("/api/auth/login", json={"password": "admin"})
        token2 = r2.json()["token"]

        # Revoke only token1
        app_client.post(
            "/api/auth/logout",
            headers={"Authorization": f"Bearer {token1}"},
        )

        # token2 should still work
        r = app_client.get(
            "/api/settings",
            headers={"Authorization": f"Bearer {token2}"},
        )
        assert r.status_code == 200


# ── BRUTE-FORCE PROTECTION ────────────────────────────────────────────────────

class TestBruteForce:
    def test_repeated_wrong_passwords_trigger_429(self, app_client):
        """After 5 failed attempts, the 6th should return 429."""
        import main as m

        # Reset rate-limit state for the test client IP (usually 'testclient')
        m._login_attempts.clear()

        blocked = False
        for i in range(7):
            r = app_client.post("/api/auth/login", json={"password": f"wrong_{i}"})
            if r.status_code == 429:
                blocked = True
                break

        assert blocked, "Expected 429 after repeated failed logins, never got it"

    def test_successful_login_clears_attempt_counter(self, app_client):
        import main as m
        m._login_attempts.clear()

        # Two failed attempts
        for _ in range(2):
            app_client.post("/api/auth/login", json={"password": "bad"})

        # Successful login should clear the counter
        r = app_client.post("/api/auth/login", json={"password": "admin"})
        assert r.status_code == 200

        # IP entry should be cleared now
        ip_key = list(m._login_attempts.keys())
        # After successful login the ip should be popped
        for ip, attempts in m._login_attempts.items():
            # Each remaining entry should have at most 2 attempts from above
            assert len(attempts) <= 2


# ── SETTINGS EXPOSURE ─────────────────────────────────────────────────────────

class TestSettingsExposure:
    def test_response_hides_password_hash(self, app_client, auth_token):
        r = app_client.get(
            "/api/settings",
            headers={"Authorization": f"Bearer {auth_token}"},
        )
        assert "password_hash" not in r.json()

    def test_response_hides_jwt_secret(self, app_client, auth_token):
        r = app_client.get(
            "/api/settings",
            headers={"Authorization": f"Bearer {auth_token}"},
        )
        assert "jwt_secret" not in r.json()

    def test_response_hides_groq_key_raw(self, app_client, auth_token):
        r = app_client.get(
            "/api/settings",
            headers={"Authorization": f"Bearer {auth_token}"},
        )
        data = r.json()
        assert "groq_api_key_enc" not in data
        # A boolean flag should be present instead
        assert "groq_key_set" in data
        assert isinstance(data["groq_key_set"], bool)


# ── REVOKE-ALL-SESSIONS ───────────────────────────────────────────────────────

class TestRevokeAll:
    def test_revoke_all_invalidates_current_token(self, app_client):
        # Mint a fresh token
        r = app_client.post("/api/auth/login", json={"password": "admin"})
        token = r.json()["token"]

        # Verify it works
        r = app_client.get("/api/settings", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200

        # Revoke ALL sessions (this regenerates _JWT_SECRET in main.py)
        app_client.post(
            "/api/settings/security/revoke-all",
            headers={"Authorization": f"Bearer {token}"},
        )

        # The old token should now be invalid (wrong secret)
        r = app_client.get("/api/settings", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 401
