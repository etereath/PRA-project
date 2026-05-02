from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class TableValidationIssue:
    row_number: int
    field_name: str
    message: str


class ValidationError(Exception):
    """Raised when workbook input or business data is invalid."""


class TableValidationError(ValidationError):
    """Raised when editable table content contains field-level validation issues."""

    def __init__(self, table_name: str, issues: list[TableValidationIssue]):
        self.table_name = table_name
        self.issues = issues
        summary = f"{table_name}: {len(issues)} validation issue(s)"
        super().__init__(summary)


class AIProviderError(Exception):
    """Raised when an AI suggestion provider fails."""
