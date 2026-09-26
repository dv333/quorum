"""Splitting result lists into pages."""


def paginate(items, page, per_page=20):
    """Return one page of items; pages start at 1."""
    if page < 1:
        raise ValueError("page starts at 1")
    start = (page - 1) * per_page
    return items[start : start + per_page]


def page_count(total, per_page=20):
    """How many pages `total` items fill."""
    return (total + per_page - 1) // per_page
