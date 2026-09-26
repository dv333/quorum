"""Splitting result lists into pages."""

MAX_PER_PAGE = 100


def paginate(items, page, per_page=20):
    """Return one page of items and whether another page follows; pages start at 1."""
    per_page = min(per_page, MAX_PER_PAGE)
    start = page * per_page
    end = start + per_page
    return items[start:end], end <= len(items)


def page_count(total, per_page=20):
    """How many pages `total` items fill."""
    return total // min(per_page, MAX_PER_PAGE)
