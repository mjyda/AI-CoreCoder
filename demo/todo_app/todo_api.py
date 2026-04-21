from fastapi import APIRouter
from typing import List
from .todo_service import list_todos, get_todo, Todo

router = APIRouter(prefix="/todos")

@router.get("/", response_model=List[Todo])
async def list_todos_endpoint():
    return list_todos()

@router.get("/{todo_id}", response_model=Todo)
async def get_todo_endpoint(todo_id: int):
    todo = get_todo(todo_id)
    if todo is None:
        return {"error": "Todo not found"}
    return todo