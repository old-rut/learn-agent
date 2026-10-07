import json
import ast
from typing import Annotated, Literal

from langchain_core.tools import tool
from pydantic import BaseModel, Field


# -- New in s05: structured state the model updates --

class Todo(BaseModel):
    """一条待办。字段约束会自动进入工具的 JSON Schema，模型可以直接看到。"""
    content: str = Field(min_length=1, description="what needs to be done")
    status: Literal["pending", "in_progress", "completed"] = Field(
        default="pending", description="current state"
    )


class TodoManager:
    def __init__(self):
        self.items: list[dict] = []

    def update(self, todos: list | str) -> str:
        if isinstance(todos, str):
            try:
                todos = json.loads(todos)
            except json.JSONDecodeError:
                try:
                    todos = ast.literal_eval(todos)
                except (SyntaxError, ValueError) as e:
                    raise ValueError("todos must be a list or JSON array string") from e

        if not isinstance(todos, list):
            raise ValueError("todos must be a list")
        if len(todos) > 20:
            raise ValueError("Max 20 todos allowed")

        validated = []
        in_progress_count = 0
        for index, todo in enumerate(todos):
            if not isinstance(todo, dict):
                raise ValueError(f"todos[{index}] must be an object")

            content = str(todo.get("content", "")).strip()
            status = str(todo.get("status", "pending")).lower()
            if not content:
                raise ValueError(f"todos[{index}] requires content")
            if status not in ("pending", "in_progress", "completed"):
                raise ValueError(f"todos[{index}] has invalid status '{status}'")
            if status == "in_progress":
                in_progress_count += 1
            validated.append({"content": content, "status": status})

        if in_progress_count > 1:
            raise ValueError("Only one todo can be in_progress at a time")

        self.items = validated
        return self.render()

    def render(self) -> str:
        if not self.items:
            return "No todos."

        lines = []
        for todo in self.items:
            marker = {
                "pending": "[ ]",
                "in_progress": "[>]",
                "completed": "[x]",
            }[todo["status"]]
            lines.append(f"{marker} {todo['content']}")

        done = sum(todo["status"] == "completed" for todo in self.items)
        lines.append(f"\n({done}/{len(self.items)} completed)")
        return "\n".join(lines)


TODO = TodoManager()

@tool
def todo_writer(todos: Annotated[list[Todo], Field(max_length=20)] | str) -> str:
    """Create and manage a task list for your current coding session."""

    if isinstance(todos, list):
        # LangChain 已按 Todo 模型校验并实例化，转回 dict 交给 update() 做业务规则校验
        todos = [t.model_dump() if isinstance(t, Todo) else t for t in todos]
    try:
        output = TODO.update(todos)
    except ValueError as e:
        return f"Error: {e}"
    print(f"\n\033[33m## Current Tasks\033[0m\n{output}")
    return output