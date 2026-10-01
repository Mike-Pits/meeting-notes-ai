from datetime import date
from typing import Literal
from pydantic import BaseModel, Field, ConfigDict


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MeetingInput(Strict):
    title: str = Field(min_length=1, max_length=200)
    meeting_date: date
    text: str = Field(default="", max_length=200_000)
    version: int = 0


class Decision(Strict):
    text: str = Field(min_length=1)
    quote: str = Field(min_length=1)


class Task(Strict):
    text: str = Field(min_length=1)
    quote: str = Field(min_length=1)
    owner: str | None = None
    due_raw: str | None = None
    due_date: date | None = None
    status: Literal["Не начато", "В работе", "Выполнено"] = "Не начато"
    owner_confirmed: bool = False
    due_confirmed: bool = False
    manual: bool = False


class Result(Strict):
    summary: str
    participants: list[str] = Field(default_factory=list)
    topics: list[str] = Field(default_factory=list)
    decisions: list[Decision] = Field(default_factory=list)
    tasks: list[Task] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)


class ResultInput(Strict):
    result: Result
    version: int
