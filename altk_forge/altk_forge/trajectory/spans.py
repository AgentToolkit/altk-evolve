"""Walking the span tree of a trajectory."""


def walk(span):
    """Every span of the tree under `span`, `span` first, depth first."""
    yield span
    for child in span.get("children") or []:
        yield from walk(child)


def walk_with_parent(span, parent=None):
    """(span, its parent) for every span of the tree under `span`; the root's parent is None."""
    yield span, parent
    for child in span.get("children") or []:
        yield from walk_with_parent(child, span)
