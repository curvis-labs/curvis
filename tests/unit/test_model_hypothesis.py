"""Property-based tests: random command sequences, undo/redo and the dependency graph."""

import copy
from collections import deque

from hypothesis import given
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, initialize, invariant, rule

from curvis.core import (
    UNDO_LIMIT,
    AddColumn,
    AddTable,
    BulkRename,
    Command,
    CommandError,
    CycleError,
    DependencyGraph,
    Document,
    GroupColumns,
    MoveColumn,
    RemoveColumn,
    RemoveTable,
    RenameCategoryFactor,
    RenameColumn,
    RenameTable,
    ReorderColumns,
    SetCategory,
    SetCellBlock,
    SetCellValue,
    SetColumnRole,
    UndoStack,
    UngroupColumns,
)
from curvis.data import CellValue, Column, ColumnRole, Table

NAMES = st.sampled_from(
    ["A", "a", "B", "Dose", "dOSE", "Resp", "T0", "t0", "C1", "c1", "", " "]
) | st.text(max_size=4)
ROLES = st.sampled_from(list(ColumnRole))
NUMBERS = st.floats(allow_nan=True, allow_infinity=True) | st.integers(-1000, 1000)
CELLS: st.SearchStrategy[CellValue] = (
    st.none()
    | NUMBERS
    | st.sampled_from(["", "1.5", " 2 ", "x", "nan", "-0"])
    | st.text(max_size=3)
)


@st.composite
def columns(draw: st.DrawFn, name: str, n_rows: int) -> Column:
    role = draw(ROLES)
    if role.is_numeric:
        values: list[CellValue] = draw(
            st.lists(st.none() | NUMBERS, min_size=n_rows, max_size=n_rows)
        )
    else:
        values = draw(st.lists(st.none() | st.text(max_size=3), min_size=n_rows, max_size=n_rows))
    return Column.new(name, role, values, unit=draw(st.sampled_from(["", "s", "µM"])))


@st.composite
def tables(draw: st.DrawFn, name: str) -> Table:
    n_rows = draw(st.integers(0, 5))
    n_cols = draw(st.integers(0, 4))
    return Table(name, [draw(columns(f"C{i}", n_rows)) for i in range(n_cols)])


@st.composite
def documents(draw: st.DrawFn) -> Document:
    n_tables = draw(st.integers(0, 3))
    return Document(tables=[draw(tables(f"T{i}")) for i in range(n_tables)])


def draw_command(data: st.DataObject, doc: Document) -> Command:
    """Draw a command for the current state. It may be invalid (then it must be rejected)."""
    kinds = ["add_table"]
    if doc.tables:
        kinds += ["remove_table", "rename_table", "add_column"]
    tables_with_columns = [t for t in doc.tables if t.columns]
    if tables_with_columns:
        kinds += ["remove_column", "move_column", "rename_column", "cell", "block", "role"] * 2
        kinds += ["group", "group", "ungroup", "category", "reorder", "bulk_rename", "factor"]
    kind = data.draw(st.sampled_from(kinds))
    if kind == "add_table":
        n = len(doc.tables)
        return AddTable(data.draw(tables(data.draw(NAMES))), data.draw(st.integers(0, n + 1)))
    if kind in ("remove_table", "rename_table", "add_column"):
        table = data.draw(st.sampled_from(doc.tables))
        if kind == "remove_table":
            return RemoveTable(table.id)
        if kind == "rename_table":
            return RenameTable(table.id, data.draw(NAMES))
        new = data.draw(columns(data.draw(NAMES), data.draw(st.integers(0, 7))))
        return AddColumn(table.id, new, data.draw(st.none() | st.integers(0, len(table.columns))))

    table = data.draw(st.sampled_from(tables_with_columns))
    column = data.draw(st.sampled_from(table.columns))
    n_cols, n_rows = len(table.columns), table.n_rows
    some_columns = data.draw(st.lists(st.sampled_from([c.id for c in table.columns]), max_size=4))
    group_ids = [g.id for g in table.replicate_groups]
    if kind == "group":
        return GroupColumns(table.id, some_columns, data.draw(NAMES))
    if kind == "ungroup":
        return UngroupColumns(table.id, some_columns)
    if kind == "category":
        some_groups = (
            data.draw(st.lists(st.sampled_from(group_ids), max_size=3)) if group_ids else []
        )
        return SetCategory(table.id, some_groups, data.draw(st.none() | NAMES))
    if kind == "reorder":
        return ReorderColumns(table.id, data.draw(st.permutations([c.id for c in table.columns])))
    if kind == "factor":
        return RenameCategoryFactor(table.id, data.draw(NAMES))
    if kind == "bulk_rename":
        return BulkRename(
            table.id,
            {c: data.draw(NAMES) for c in some_columns},
            {g: data.draw(NAMES) for g in data.draw(st.lists(st.sampled_from(group_ids)))}
            if group_ids
            else {},
        )
    if kind == "remove_column":
        return RemoveColumn(table.id, column.id)
    if kind == "move_column":
        return MoveColumn(table.id, column.id, data.draw(st.integers(0, n_cols)))
    if kind == "rename_column":
        return RenameColumn(table.id, column.id, data.draw(NAMES))
    if kind == "role":
        return SetColumnRole(table.id, column.id, data.draw(ROLES))
    if kind == "cell":
        return SetCellValue(
            table.id, column.id, data.draw(st.integers(0, n_rows + 3)), data.draw(CELLS)
        )
    width = data.draw(st.integers(1, n_cols))
    block = data.draw(
        st.lists(st.lists(CELLS, min_size=width, max_size=width), min_size=1, max_size=4)
    )
    return SetCellBlock(
        table.id,
        data.draw(st.integers(0, n_rows + 2)),
        data.draw(st.integers(0, n_cols - width + 1)),  # may run one past the end
        block,
    )


def try_execute(doc: Document, command: Command) -> bool:
    """Execute ``command``; if it is rejected, check that nothing changed."""
    before = copy.deepcopy(doc.tables)
    log_length = len(doc.change_log)
    try:
        doc.execute(command)
    except CommandError:
        assert doc.tables == before
        assert len(doc.change_log) == log_length
        return False
    for table in doc.tables:
        table.check()
        assert all(not c.values.flags.writeable for c in table.columns)
    return True


@given(doc=documents(), data=st.data())
def test_random_commands_then_full_undo_restores_document(
    doc: Document, data: st.DataObject
) -> None:
    original = copy.deepcopy(doc.tables)
    n_steps = data.draw(st.integers(1, UNDO_LIMIT))
    applied = 0
    for _ in range(n_steps):
        if try_execute(doc, draw_command(data, doc)):
            applied += 1
    final = copy.deepcopy(doc.tables)

    assert doc.undo_stack.index == applied
    while doc.undo_stack.can_undo:
        doc.undo()
    assert doc.tables == original

    while doc.undo_stack.can_redo:
        doc.redo()
    assert doc.tables == final
    assert len(doc.change_log) == 3 * applied


class UndoRedoMachine(RuleBasedStateMachine):
    """Mixes execute, undo and redo; the document must always equal the expected snapshot."""

    limit = UNDO_LIMIT

    @initialize(doc=documents())
    def start(self, doc: Document) -> None:
        self.doc = doc
        self.doc.undo_stack = UndoStack(doc, limit=self.limit)
        self.history = [copy.deepcopy(doc.tables)]
        self.position = 0
        self.log_length = 0

    @rule(data=st.data())
    def execute(self, data: st.DataObject) -> None:
        if try_execute(self.doc, draw_command(data, self.doc)):
            self.history = [*self.history[: self.position + 1], copy.deepcopy(self.doc.tables)]
            self.position += 1
            if len(self.history) > self.limit + 1:
                del self.history[0]
                self.position -= 1
            self.log_length += 1

    @rule()
    def undo(self) -> None:
        if self.position > 0:
            self.position -= 1
            self.log_length += 1
        self.doc.undo()

    @rule()
    def redo(self) -> None:
        if self.position < len(self.history) - 1:
            self.position += 1
            self.log_length += 1
        self.doc.redo()

    @invariant()
    def matches_expected_state(self) -> None:
        assert self.doc.tables == self.history[self.position]
        assert len(self.doc.change_log) == self.log_length
        assert self.doc.undo_stack.index == self.position
        assert self.doc.undo_stack.count == len(self.history) - 1


class SmallLimitUndoRedoMachine(UndoRedoMachine):
    """Same, with a tiny limit so dropping the oldest step is exercised often."""

    limit = 3


TestUndoRedo = UndoRedoMachine.TestCase
TestSmallLimitUndoRedo = SmallLimitUndoRedoMachine.TestCase


# --- dependency graph


def reachable(edges: dict[str, set[str]], starts: set[str]) -> set[str]:
    """Brute-force: all nodes that read (directly or not) one of ``starts``."""
    readers: dict[str, set[str]] = {}
    for node, reads in edges.items():
        for r in reads:
            readers.setdefault(r, set()).add(node)
    seen: set[str] = set()
    queue = deque(starts)
    while queue:
        for reader in readers.get(queue.popleft(), set()):
            if reader not in seen:
                seen.add(reader)
                queue.append(reader)
    return seen


@given(
    edges=st.lists(st.tuples(st.integers(0, 9), st.integers(0, 9)), max_size=30),
    changed=st.sets(st.integers(0, 9), min_size=1, max_size=3),
)
def test_graph_matches_brute_force(edges: list[tuple[int, int]], changed: set[int]) -> None:
    """Add reads one at a time; cycles are rejected exactly when brute force finds one."""
    graph = DependencyGraph()
    model: dict[str, set[str]] = {}
    for reader_i, source_i in edges:
        reader, source = f"n{reader_i}", f"n{source_i}"
        new_reads = model.get(reader, set()) | {source}
        creates_cycle = reader == source or source in reachable(model, {reader})
        if creates_cycle:
            try:
                graph.set_reads(reader, sorted(new_reads))
            except CycleError as exc:
                assert exc.path[0] == exc.path[-1] == reader
            else:
                raise AssertionError("cycle was not rejected")
        else:
            graph.set_reads(reader, sorted(new_reads))
            model[reader] = new_reads
        assert {n: set(graph.reads(n)) for n in graph.nodes} == model

    starts = {f"n{i}" for i in changed}
    order = graph.downstream(starts)
    assert len(order) == len(set(order))
    assert set(order) == reachable(model, starts)
    position = {node: i for i, node in enumerate(order)}
    for node in order:
        for source in model[node]:
            if source in position:
                assert position[source] < position[node]
