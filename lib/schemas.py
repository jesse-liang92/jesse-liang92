"""
Pydantic v2 schemas for all LLM response types.

Each agent that uses the LLM imports its schema from here.
Keeping schemas centralized makes model-swap validation easy:
run test_structural against all schemas in one pass.
"""

from typing import Any
from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# morning_digest
# ---------------------------------------------------------------------------

class MorningDigestResponse(BaseModel):
    headline: str = Field(description="One-sentence summary of the day")
    time_sensitive: list[str] = Field(
        default_factory=list,
        description="Items needing immediate attention",
    )
    schedule_conflicts: list[str] = Field(
        default_factory=list,
        description="Conflicting event pairs, empty if none",
    )
    weather_note: str | None = Field(
        default=None,
        description="Weather note only if actionable, else null",
    )
    prep_needed: list[str] = Field(
        default_factory=list,
        description="Meetings requiring preparation",
    )
    todo_priorities: list[str] = Field(
        default_factory=list,
        description="Top 3 to-do items for today",
    )
    full_briefing: str = Field(description="Natural language morning briefing, ≤200 words")


# ---------------------------------------------------------------------------
# commute_ping  (LLM used only for ambiguous location resolution)
# ---------------------------------------------------------------------------

class LocationResolutionResponse(BaseModel):
    resolved_address: str | None = Field(
        description="Best-guess physical address, or null if unresolvable"
    )
    confidence: float = Field(ge=0.0, le=1.0, description="0.0–1.0 confidence score")
    is_virtual: bool = Field(
        default=False,
        description="True if location indicates a virtual meeting (Zoom/Teams/etc.)",
    )


# ---------------------------------------------------------------------------
# discord_reminders
# ---------------------------------------------------------------------------

class ReminderParseResponse(BaseModel):
    task: str = Field(description="What the user wants to be reminded about")
    remind_at: str | None = Field(
        description="ISO 8601 datetime for the reminder, or null if unparseable"
    )
    recurrence: str = Field(
        default="none",
        description="none | daily | weekly | monthly",
        pattern="^(none|daily|weekly|monthly)$",
    )
    confidence: float = Field(ge=0.0, le=1.0, description="0.0–1.0 confidence score")


# ---------------------------------------------------------------------------
# grocery_optimizer
# ---------------------------------------------------------------------------

class GroceryItem(BaseModel):
    item: str
    quantity: str | None = None
    note: str | None = None


class GrocerySection(BaseModel):
    name: str = Field(description="Store section name (e.g. Produce, Dairy)")
    items: list[GroceryItem]


class GroceryOptimizerResponse(BaseModel):
    sections: list[GrocerySection]
    duplicates_flagged: list[str] = Field(
        default_factory=list,
        description="Items that appear to be duplicates",
    )
    estimated_total: float | None = Field(
        default=None,
        description="Rough cost estimate in USD, or null",
    )


# ---------------------------------------------------------------------------
# finance_digest  (future)
# ---------------------------------------------------------------------------

class FinanceDigestResponse(BaseModel):
    summary: str = Field(description="One-paragraph financial summary")
    alerts: list[str] = Field(
        default_factory=list,
        description="Any notable movements or alerts",
    )
    watchlist: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Watchlist items with current price and change",
    )


# ---------------------------------------------------------------------------
# bill_monitor  (future)
# ---------------------------------------------------------------------------

class BillAlertResponse(BaseModel):
    bills_due: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Bills due within 7 days",
    )
    overdue: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Overdue bills",
    )
    summary: str = Field(description="Plain text summary of bill status")
