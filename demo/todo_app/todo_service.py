from typing import List
from pydantic import BaseModel

# Ä£ÄâÊý¾Ý´æ´¢
todos_list = [
    {"id": 1, "title": "Learn FastAPI", "completed": False},
    {"id": 2, "title": "Build a todo app", "completed": True}
]

class Todo(BaseModel):
    id: int
    title: str
    completed: bool

def list_todos():
    return todos_list

def get_todo(todo_id: int):
    for todo in todos_list:
        if todo["id"] == todo_id:
            return todo
    return None