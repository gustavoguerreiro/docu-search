"""Data access, one small class per collection.

Rules that apply to every repository here:
- Ownership is part of the query (`{_id, owner_id}`), never a check done after the read.
- Identifiers are `ObjectId`; callers convert client input with `docusearch.db.ids` first.
- There is no cross-collection atomicity. Callers that touch several collections must be written
  to tolerate a failure between the steps.
"""

MAX_PAGE_SIZE = 100


def check_page(limit: int, offset: int) -> None:
    """Reject nonsensical paging arguments instead of silently clamping them."""
    if not 1 <= limit <= MAX_PAGE_SIZE:
        raise ValueError(f"limit must be between 1 and {MAX_PAGE_SIZE}")
    if offset < 0:
        raise ValueError("offset must not be negative")
