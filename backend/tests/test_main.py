from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def test_creates_and_deletes_conversation() -> None:
    created = client.post("/api/conversations")
    assert created.status_code == 201
    conversation_id = created.json()["id"]

    deleted = client.delete(f"/api/conversations/{conversation_id}")
    assert deleted.status_code == 204


def test_turn_rejects_unknown_conversation_without_starting_codex() -> None:
    response = client.post("/api/conversations/missing/turns", json={"text": "Hello"})
    assert response.status_code == 404


def test_htmx_shell_creates_a_conversation_form() -> None:
    page = client.get("/")
    assert page.status_code == 200
    assert 'src="/static/vendor/htmx-2.0.4.min.js"' in page.text
    assert client.get("/static/vendor/htmx-2.0.4.min.js").status_code == 200

    fragment = client.post("/conversations")
    assert fragment.status_code == 200
    assert "hx-post" in fragment.text
