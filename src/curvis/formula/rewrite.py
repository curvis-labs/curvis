"""Change the column names a formula refers to, keeping the rest of its text as written."""

from collections.abc import Iterable, Iterator, Mapping

from curvis.data.names import name_key
from curvis.formula.ast import Binary, Call, ColumnRef, Node, Unary
from curvis.formula.errors import FormulaError
from curvis.formula.parser import parse


def rename_columns(text: str, renames: Mapping[str, str]) -> str:
    """``text`` with every ``[old]`` in ``renames`` (old name → new name) replaced by ``[new]``.

    Names match ignoring case, as everywhere in formulas. All references are replaced at
    once, so names can be swapped. A formula that does not parse is returned unchanged.
    """
    return _replace_refs(text, renames, "[")


def mark_deleted(text: str, names: Iterable[str]) -> str:
    """``text`` with every ``[name]`` of ``names`` written ``#DELETED[name]``.

    Used when the column a reference points at is deleted, so that the reference cannot
    silently move to another column that has (or later takes) the name. Names match
    ignoring case; each reference keeps the name as it was written. A formula that does
    not parse is returned unchanged.
    """
    return _replace_refs(text, {name: None for name in names}, "#DELETED[")


def _replace_refs(text: str, renames: Mapping[str, str | None], opening: str) -> str:
    """Rewrite the column references of ``text`` named in ``renames`` as ``opening``,
    the new name (the name as written for None) and ``]``."""
    if not renames:
        return text
    by_key = {name_key(old): new for old, new in renames.items()}
    try:
        root = parse(text).root
    except FormulaError:
        return text
    refs = [n for n in column_refs(root) if name_key(n.name) in by_key]
    for ref in sorted(refs, key=lambda n: n.span.start, reverse=True):
        name = by_key[name_key(ref.name)]
        new = opening + (ref.name if name is None else name).replace("]", "]]") + "]"
        text = text[: ref.span.start] + new + text[ref.span.end :]
    return text


def column_refs(node: Node) -> Iterator[ColumnRef]:
    """Every column reference in the tree under ``node``, in the order written."""
    match node:
        case ColumnRef():
            yield node
        case Unary(_, operand):
            yield from column_refs(operand)
        case Binary(_, left, right):
            yield from column_refs(left)
            yield from column_refs(right)
        case Call(_, args):
            for arg in args:
                yield from column_refs(arg)
