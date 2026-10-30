"""Change the column names a formula refers to, keeping the rest of its text as written."""

from collections.abc import Iterator, Mapping

from curvis.data.names import name_key
from curvis.formula.ast import Binary, Call, ColumnRef, Node, Unary
from curvis.formula.errors import FormulaError
from curvis.formula.parser import parse


def rename_columns(text: str, renames: Mapping[str, str]) -> str:
    """``text`` with every ``[old]`` in ``renames`` (old name → new name) replaced by ``[new]``.

    Names match ignoring case, as everywhere in formulas. All references are replaced at
    once, so names can be swapped. A formula that does not parse is returned unchanged.
    """
    if not renames:
        return text
    by_key = {name_key(old): new for old, new in renames.items()}
    try:
        root = parse(text).root
    except FormulaError:
        return text
    refs = [n for n in _column_refs(root) if name_key(n.name) in by_key]
    for ref in sorted(refs, key=lambda n: n.span.start, reverse=True):
        new = "[" + by_key[name_key(ref.name)].replace("]", "]]") + "]"
        text = text[: ref.span.start] + new + text[ref.span.end :]
    return text


def _column_refs(node: Node) -> Iterator[ColumnRef]:
    match node:
        case ColumnRef():
            yield node
        case Unary(_, operand):
            yield from _column_refs(operand)
        case Binary(_, left, right):
            yield from _column_refs(left)
            yield from _column_refs(right)
        case Call(_, args):
            for arg in args:
                yield from _column_refs(arg)
