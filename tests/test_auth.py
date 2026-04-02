"""Test auth endpoints: signup, login, me, invite, protected access."""


def test_signup(unauthed_client):
    resp = unauthed_client.post("/api/auth/signup", json={
        "name": "Sushant",
        "email": "sushant@test.com",
        "password": "securepass123",
        "org_name": "Team AAKNS",
    })
    assert resp.status_code == 201
    data = resp.json()
    assert "access_token" in data
    assert data["token_type"] == "bearer"


def test_signup_auto_org_name(unauthed_client):
    resp = unauthed_client.post("/api/auth/signup", json={
        "name": "Ankit",
        "email": "ankit@test.com",
        "password": "pass123",
    })
    assert resp.status_code == 201


def test_signup_duplicate_email(unauthed_client):
    unauthed_client.post("/api/auth/signup", json={
        "name": "User1",
        "email": "same@test.com",
        "password": "pass123",
    })
    resp = unauthed_client.post("/api/auth/signup", json={
        "name": "User2",
        "email": "same@test.com",
        "password": "pass456",
    })
    assert resp.status_code == 409


def test_login_success(unauthed_client):
    # Signup first
    unauthed_client.post("/api/auth/signup", json={
        "name": "Tester",
        "email": "login@test.com",
        "password": "mypassword",
    })

    # Login
    resp = unauthed_client.post("/api/auth/login", json={
        "email": "login@test.com",
        "password": "mypassword",
    })
    assert resp.status_code == 200
    assert "access_token" in resp.json()


def test_login_wrong_password(unauthed_client):
    unauthed_client.post("/api/auth/signup", json={
        "name": "Tester",
        "email": "wrong@test.com",
        "password": "correctpass",
    })

    resp = unauthed_client.post("/api/auth/login", json={
        "email": "wrong@test.com",
        "password": "wrongpass",
    })
    assert resp.status_code == 401


def test_login_nonexistent_email(unauthed_client):
    resp = unauthed_client.post("/api/auth/login", json={
        "email": "nobody@test.com",
        "password": "anything",
    })
    assert resp.status_code == 401


def test_me_with_token(unauthed_client):
    # Signup
    signup_resp = unauthed_client.post("/api/auth/signup", json={
        "name": "Profile User",
        "email": "profile@test.com",
        "password": "pass123",
        "org_name": "My Org",
    })
    token = signup_resp.json()["access_token"]

    # Get profile
    resp = unauthed_client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "Profile User"
    assert data["email"] == "profile@test.com"
    assert data["role"] == "admin"
    assert data["auth_provider"] == "local"


def test_me_without_token(unauthed_client):
    resp = unauthed_client.get("/api/auth/me")
    assert resp.status_code == 401


def test_protected_endpoint_without_token(unauthed_client):
    resp = unauthed_client.get("/api/projects")
    assert resp.status_code == 401


def test_protected_endpoint_with_token(unauthed_client):
    # Signup to get token
    signup_resp = unauthed_client.post("/api/auth/signup", json={
        "name": "Auth User",
        "email": "auth@test.com",
        "password": "pass123",
    })
    token = signup_resp.json()["access_token"]

    # Access protected endpoint
    resp = unauthed_client.get("/api/projects", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json() == []


def test_org_isolation(unauthed_client):
    # User 1 signs up and creates a project
    resp1 = unauthed_client.post("/api/auth/signup", json={
        "name": "User One",
        "email": "one@test.com",
        "password": "pass123",
        "org_name": "Org A",
    })
    token1 = resp1.json()["access_token"]

    unauthed_client.post("/api/projects", json={"name": "Org A Project"},
                         headers={"Authorization": f"Bearer {token1}"})

    # User 2 signs up (different org) and should NOT see User 1's project
    resp2 = unauthed_client.post("/api/auth/signup", json={
        "name": "User Two",
        "email": "two@test.com",
        "password": "pass123",
        "org_name": "Org B",
    })
    token2 = resp2.json()["access_token"]

    projects = unauthed_client.get("/api/projects",
                                   headers={"Authorization": f"Bearer {token2}"})
    assert projects.status_code == 200
    assert len(projects.json()) == 0  # User 2 sees nothing from Org A


def test_invite_member(unauthed_client):
    # Admin signs up
    signup_resp = unauthed_client.post("/api/auth/signup", json={
        "name": "Admin",
        "email": "admin@test.com",
        "password": "pass123",
        "org_name": "Team",
    })
    token = signup_resp.json()["access_token"]

    # Invite a team member
    resp = unauthed_client.post(
        "/api/auth/invite?email=dev@test.com&name=Developer&role=developer",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert "temp_password" in data
    assert data["message"] == "Invited dev@test.com as developer"

    # Invited user can login with temp password
    login_resp = unauthed_client.post("/api/auth/login", json={
        "email": "dev@test.com",
        "password": data["temp_password"],
    })
    assert login_resp.status_code == 200
