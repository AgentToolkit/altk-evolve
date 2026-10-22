from altk_forge.trajectory.spans import walk, walk_with_parent

TREE = {
    "span_id": "turn",
    "children": [{"span_id": "a", "children": [{"span_id": "a1"}, {"span_id": "a2", "children": []}]}, {"span_id": "b"}],
}


def test_walk_is_depth_first_from_the_root():
    assert [s["span_id"] for s in walk(TREE)] == ["turn", "a", "a1", "a2", "b"]


def test_walk_with_parent_pairs_every_span_with_its_parent():
    pairs = [(s["span_id"], p and p["span_id"]) for s, p in walk_with_parent(TREE)]
    assert pairs == [("turn", None), ("a", "turn"), ("a1", "a"), ("a2", "a"), ("b", "turn")]


def test_a_span_without_children_is_a_leaf():
    assert [s["span_id"] for s in walk({"span_id": "x", "children": None})] == ["x"]
