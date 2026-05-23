"""Reshape the flat notebook rows into the nested forest the tree pane shows.

:meth:`core.repository.Repository.list_notebooks` returns notebooks as a flat
list, each carrying a ``parent_id`` pointing at the notebook it nests under (or
``None`` for a top-level notebook). The notebook *tree* pane needs that list
reshaped into a forest of parent → children, which is what
:func:`build_notebook_tree` does.

Pure Python, no Qt (CLAUDE.md): the reshaping and ordering logic lives here,
unit-tested without a Qt runtime; ``ui/main_window.py`` only walks the resulting
:class:`NotebookNode`\\ s into ``QTreeWidgetItem``\\ s.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from core.repository import Notebook


@dataclass(frozen=True)
class NotebookNode:
    """A notebook together with its ordered child nodes in the tree.

    ``children`` is a (possibly empty) tuple of the notebooks nested directly
    under :attr:`notebook`, each itself a :class:`NotebookNode`, so the whole
    forest is reachable by walking from the roots.
    """

    notebook: Notebook
    children: tuple[NotebookNode, ...] = field(default_factory=tuple)


def _sort_key(notebook: Notebook) -> tuple[str, int]:
    # Mirror Repository.list_notebooks (name case-insensitive, then id) so the
    # tree's sibling order matches the flat list elsewhere in the app.
    return (notebook.name.casefold(), notebook.id)


def build_notebook_tree(notebooks: list[Notebook]) -> list[NotebookNode]:
    """Reshape a flat notebook list into a nested forest of :class:`NotebookNode`.

    Siblings — including the top-level roots — are ordered by name
    (case-insensitive) then id, matching
    :meth:`core.repository.Repository.list_notebooks`.

    Robust against malformed data so the UI can never loop or lose a notebook:

    * A notebook whose ``parent_id`` matches no notebook in the list is treated
      as a **root** (surfaced at the top level, never dropped).
    * A parent cycle (e.g. ``A→B→A`` — which the data layer does not currently
      prevent) is broken: the chain is followed until it would revisit a
      notebook, and any notebook left unreachable from a genuine root is then
      surfaced at the root so nothing is ever omitted.
    """
    by_id: dict[int, Notebook] = {nb.id: nb for nb in notebooks}

    # Group children by their effective parent: a parent_id pointing outside the
    # set (or None) makes the notebook a root.
    children_of: dict[int | None, list[Notebook]] = {}
    for nb in notebooks:
        parent = nb.parent_id if nb.parent_id in by_id else None
        children_of.setdefault(parent, []).append(nb)

    emitted: set[int] = set()

    def build(nb: Notebook, ancestry: frozenset[int]) -> NotebookNode:
        emitted.add(nb.id)
        child_nodes: list[NotebookNode] = []
        for child in sorted(children_of.get(nb.id, ()), key=_sort_key):
            # Skip a child that is already one of our ancestors (cycle) or has
            # already been placed — either would otherwise recurse forever.
            if child.id in ancestry or child.id in emitted:
                continue
            child_nodes.append(build(child, ancestry | {nb.id}))
        return NotebookNode(nb, tuple(child_nodes))

    roots = [build(nb, frozenset()) for nb in sorted(children_of.get(None, ()), key=_sort_key)]

    # Any notebook not reached above is trapped in a cycle with no path to a
    # root; surface it at the root (breaking the cycle) so it is never lost.
    for nb in sorted(by_id.values(), key=_sort_key):
        if nb.id not in emitted:
            roots.append(build(nb, frozenset()))

    return roots


def would_create_cycle(
    notebooks: list[Notebook],
    notebook_id: int,
    new_parent_id: int | None,
) -> bool:
    """Return ``True`` if re-parenting ``notebook_id`` under ``new_parent_id``
    would create a cycle.

    Moving a notebook back to the top level (``new_parent_id is None``) can never
    create a cycle. Otherwise a cycle forms exactly when ``new_parent_id`` is
    ``notebook_id`` itself (a notebook can't be its own parent) or one of
    ``notebook_id``'s descendants — making ``notebook_id`` an ancestor of its own
    new parent.

    Detected by walking *up* the parent chain from ``new_parent_id``: if that
    chain reaches ``notebook_id``, the move is a cycle. A ``new_parent_id`` that
    matches no notebook in the list would become an effective root (the same way
    :func:`build_notebook_tree` treats unknown parents), so its chain can't reach
    ``notebook_id`` and the move is allowed. A ``seen`` set keeps the walk finite
    even if the existing data already contains a cycle.

    This is the guard the data layer lacks: :meth:`core.repository.Repository.update_notebook`
    will set any ``parent_id`` it is given, so callers re-parenting a notebook must
    check this first.
    """
    if new_parent_id is None:
        return False

    by_id: dict[int, Notebook] = {nb.id: nb for nb in notebooks}
    current: int | None = new_parent_id
    seen: set[int] = set()
    while current is not None and current not in seen:
        if current == notebook_id:
            return True
        seen.add(current)
        parent = by_id.get(current)
        current = parent.parent_id if parent is not None else None
    return False
