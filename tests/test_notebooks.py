"""Tests for the Qt-free notebook-tree builder (ROADMAP.md M4).

:func:`core.notebooks.build_notebook_tree` reshapes the flat ``parent_id`` list
from the repository into the nested forest the tree pane renders. These are pure
Python (no Qt), so they run in CI: they pin sibling ordering, nesting, and the
defensive handling of malformed data (orphans and cycles) that keeps the UI from
ever looping or dropping a notebook.
"""

from __future__ import annotations

from core.notebooks import NotebookNode, build_notebook_tree, would_create_cycle
from core.repository import Notebook


def _nb(notebook_id: int, name: str, parent_id: int | None = None) -> Notebook:
    """A Notebook value object with throwaway timestamps."""
    return Notebook(
        id=notebook_id,
        name=name,
        parent_id=parent_id,
        created_at="2026-01-01 00:00:00",
        updated_at="2026-01-01 00:00:00",
    )


def _all_ids(forest: list[NotebookNode]) -> set[int]:
    """Every notebook id reachable in the forest (for no-loss assertions)."""
    ids: set[int] = set()
    stack = list(forest)
    while stack:
        node = stack.pop()
        ids.add(node.notebook.id)
        stack.extend(node.children)
    return ids


def test_empty_list_yields_empty_forest():
    assert build_notebook_tree([]) == []


def test_flat_notebooks_are_all_roots_with_no_children():
    forest = build_notebook_tree([_nb(1, "Alpha"), _nb(2, "Beta")])
    assert [node.notebook.name for node in forest] == ["Alpha", "Beta"]
    assert all(node.children == () for node in forest)


def test_children_nest_under_their_parent():
    forest = build_notebook_tree(
        [_nb(1, "Parent"), _nb(2, "Child", parent_id=1)]
    )
    assert len(forest) == 1
    parent = forest[0]
    assert parent.notebook.id == 1
    assert [c.notebook.id for c in parent.children] == [2]


def test_multi_level_nesting():
    forest = build_notebook_tree(
        [
            _nb(1, "Root"),
            _nb(2, "Mid", parent_id=1),
            _nb(3, "Leaf", parent_id=2),
        ]
    )
    root = forest[0]
    mid = root.children[0]
    assert mid.notebook.id == 2
    assert [c.notebook.id for c in mid.children] == [3]


def test_siblings_ordered_by_name_case_insensitive_then_id():
    # Roots and children both sort by casefolded name, with id breaking ties.
    forest = build_notebook_tree(
        [
            _nb(1, "banana"),
            _nb(2, "Apple"),
            _nb(3, "apple"),  # same name as #2 (case-insensitive) → id breaks tie
        ]
    )
    assert [node.notebook.id for node in forest] == [2, 3, 1]


def test_child_siblings_are_also_ordered():
    forest = build_notebook_tree(
        [
            _nb(1, "Parent"),
            _nb(2, "Zebra", parent_id=1),
            _nb(3, "Aardvark", parent_id=1),
        ]
    )
    assert [c.notebook.name for c in forest[0].children] == ["Aardvark", "Zebra"]


def test_notebook_with_unknown_parent_is_surfaced_at_root():
    # parent_id points at a notebook not in the list → treated as a root, not
    # dropped.
    forest = build_notebook_tree([_nb(5, "Orphan", parent_id=999)])
    assert [node.notebook.id for node in forest] == [5]
    assert forest[0].children == ()


def test_two_node_cycle_is_broken_and_nothing_is_lost():
    # A→B→A: neither has a path to a real root. The builder must not loop, and
    # must still surface both notebooks exactly once.
    forest = build_notebook_tree(
        [_nb(1, "A", parent_id=2), _nb(2, "B", parent_id=1)]
    )
    assert _all_ids(forest) == {1, 2}


def test_self_parent_cycle_is_handled():
    forest = build_notebook_tree([_nb(1, "Self", parent_id=1)])
    assert _all_ids(forest) == {1}
    # Surfaced exactly once (not nested under itself).
    assert len(forest) == 1
    assert forest[0].children == ()


def test_every_notebook_appears_exactly_once():
    notebooks = [
        _nb(1, "Root"),
        _nb(2, "Mid", parent_id=1),
        _nb(3, "Leaf", parent_id=2),
        _nb(4, "OtherRoot"),
        _nb(5, "Orphan", parent_id=999),
    ]
    forest = build_notebook_tree(notebooks)
    assert _all_ids(forest) == {1, 2, 3, 4, 5}


# -- would_create_cycle (re-parent guard) ---------------------------------

# A three-level chain reused across the cycle tests: 1 → 2 → 3 (Root/Mid/Leaf).
_CHAIN = [
    _nb(1, "Root"),
    _nb(2, "Mid", parent_id=1),
    _nb(3, "Leaf", parent_id=2),
]


def test_reparenting_to_root_never_cycles():
    # Moving any notebook back to the top level (None) is always safe.
    assert would_create_cycle(_CHAIN, notebook_id=2, new_parent_id=None) is False


def test_notebook_cannot_become_its_own_parent():
    assert would_create_cycle(_CHAIN, notebook_id=2, new_parent_id=2) is True


def test_moving_under_a_direct_child_is_a_cycle():
    # 1 under 2 (its child) would make 1 a descendant of itself.
    assert would_create_cycle(_CHAIN, notebook_id=1, new_parent_id=2) is True


def test_moving_under_a_deep_descendant_is_a_cycle():
    # 1 under 3 (its grandchild) — the upward walk from 3 reaches 1.
    assert would_create_cycle(_CHAIN, notebook_id=1, new_parent_id=3) is True


def test_moving_under_an_ancestor_or_sibling_is_allowed():
    # 3 (the leaf) under 1 (its grandparent) is a legal re-parent, not a cycle.
    assert would_create_cycle(_CHAIN, notebook_id=3, new_parent_id=1) is False
    # An unrelated notebook is a fine target too.
    notebooks = [*_CHAIN, _nb(4, "Other")]
    assert would_create_cycle(notebooks, notebook_id=2, new_parent_id=4) is False


def test_unknown_target_parent_is_allowed():
    # A parent id matching no notebook becomes an effective root → no cycle.
    assert would_create_cycle(_CHAIN, notebook_id=2, new_parent_id=999) is False


def test_does_not_loop_on_preexisting_cycle():
    # The stored data is already malformed (A↔B); the guard must still terminate.
    bad = [_nb(1, "A", parent_id=2), _nb(2, "B", parent_id=1)]
    # Target 1 is reachable from itself via the bad chain → reported as a cycle.
    assert would_create_cycle(bad, notebook_id=1, new_parent_id=2) is True
    # A target unrelated to notebook 3 still terminates and is allowed.
    assert would_create_cycle(bad, notebook_id=3, new_parent_id=2) is False
