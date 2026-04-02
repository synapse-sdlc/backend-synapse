"""Test project CRUD endpoints."""


def test_create_project(client):
    resp = client.post("/api/projects", json={"name": "test-project"})
    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == "test-project"
    assert data["analysis_status"] == "pending"
    assert data["github_url"] is None
    assert "id" in data


def test_create_project_with_github(client):
    resp = client.post("/api/projects", json={
        "name": "todo-app",
        "github_url": "https://github.com/user/todo-app"
    })
    assert resp.status_code == 201
    data = resp.json()
    assert data["github_url"] == "https://github.com/user/todo-app"


def test_list_projects_empty(client):
    resp = client.get("/api/projects")
    assert resp.status_code == 200
    assert resp.json() == []


def test_list_projects(client):
    client.post("/api/projects", json={"name": "project-1"})
    client.post("/api/projects", json={"name": "project-2"})
    resp = client.get("/api/projects")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2


def test_get_project(client):
    create_resp = client.post("/api/projects", json={"name": "my-project"})
    project_id = create_resp.json()["id"]

    resp = client.get(f"/api/projects/{project_id}")
    assert resp.status_code == 200
    assert resp.json()["name"] == "my-project"


def test_get_project_not_found(client):
    resp = client.get("/api/projects/00000000-0000-0000-0000-000000000000")
    assert resp.status_code == 404
