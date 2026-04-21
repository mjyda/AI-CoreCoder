from fastapi import FastAPI
from typing import List
from pydantic import BaseModel
from .todo_api import router as todo_router

app = FastAPI()

# Ä£ÄâÊý¾Ý´æ´¢
todos_list = [
    {"id": 1, "title": "Learn FastAPI", "completed": False},
    {"id": 2, "title": "Build a todo app", "completed": True}
]

class Todo(BaseModel):
    id: int
    title: str
    completed: bool

@app.get("/")
async def root():
    return {"message": "Welcome to Todo App"}

app.include_router(todo_router)