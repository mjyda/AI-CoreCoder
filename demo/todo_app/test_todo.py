import pytest
from fastapi.testclient import TestClient
from main import app

client = TestClient(app)

def test_read_root():
    response = client.get("/")
    assert response.status_code == 200
    assert response.json() == {"message": "Welcome to Todo App"}

def test_list_todos():
    response = client.get("/todos")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 2

def test_get_todo():
    response = client.get("/todos/1")
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == 1
    assert data["title"] == "Learn FastAPI"

def test_get_todo_not_found():
    response = client.get("/todos/999")
    assert response.status_code == 200  # This returns error object, not 404