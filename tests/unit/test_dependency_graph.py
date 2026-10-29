"""Tests for the dependency graph."""

import pytest

from curvis.core import CycleError, DependencyGraph


def test_chain() -> None:
    g = DependencyGraph()
    g.set_reads("c", ["b"])
    g.set_reads("b", ["a"])
    assert g.downstream("a") == ["b", "c"]
    assert g.downstream("b") == ["c"]
    assert g.downstream("c") == []


def test_diamond_respects_order() -> None:
    g = DependencyGraph()
    g.set_reads("d", ["b", "c"])
    g.set_reads("c", ["a"])
    g.set_reads("b", ["a"])
    order = g.downstream("a")
    assert set(order) == {"b", "c", "d"}
    assert order[-1] == "d"
    assert order == ["c", "b", "d"]  # ties broken by declaration order


def test_only_downstream_nodes_are_returned() -> None:
    g = DependencyGraph()
    g.set_reads("x", ["a"])
    g.set_reads("y", ["b"])
    g.set_reads("z", ["x", "y"])
    assert g.downstream("a") == ["x", "z"]
    assert g.downstream("unknown") == []


def test_multiple_changed_nodes() -> None:
    g = DependencyGraph()
    g.set_reads("x", ["a"])
    g.set_reads("y", ["b"])
    g.set_reads("z", ["x", "y"])
    assert g.downstream(["a", "b"]) == ["x", "y", "z"]


def test_changed_node_downstream_of_another_changed_node_is_included() -> None:
    g = DependencyGraph()
    g.set_reads("b", ["a"])
    g.set_reads("c", ["b"])
    assert g.downstream(["a", "b"]) == ["b", "c"]
    assert g.downstream(["b"]) == ["c"]


def test_replacing_reads() -> None:
    g = DependencyGraph()
    g.set_reads("calc", ["a"])
    g.set_reads("calc", ["b", "b"])
    assert g.reads("calc") == ("b",)
    assert g.downstream("a") == []
    assert g.downstream("b") == ["calc"]
    assert g.readers("a") == ()
    assert g.readers("b") == ("calc",)


def test_remove_node() -> None:
    g = DependencyGraph()
    g.set_reads("b", ["a"])
    g.set_reads("c", ["b"])
    g.remove_node("b")
    assert "b" not in g
    assert g.downstream("a") == []
    assert g.downstream("b") == ["c"]  # c still reads b, now a plain source
    g.remove_node("never-declared")
    assert g.nodes == ("c",)


def test_self_read_is_a_cycle() -> None:
    g = DependencyGraph()
    with pytest.raises(CycleError, match="Circular dependency: a → a"):
        g.set_reads("a", ["a"])
    assert "a" not in g


def test_direct_cycle_is_rejected_and_graph_unchanged() -> None:
    g = DependencyGraph()
    g.set_reads("b", ["a"])
    with pytest.raises(CycleError) as info:
        g.set_reads("a", ["b"])
    assert str(info.value) == "Circular dependency: a → b → a"
    assert info.value.path == ["a", "b", "a"]
    assert "a" not in g
    assert g.downstream("a") == ["b"]


def test_indirect_cycle_names_the_path() -> None:
    g = DependencyGraph()
    g.set_reads("b", ["a"])
    g.set_reads("c", ["b"])
    g.set_reads("d", ["c"])
    g.set_reads("a", ["x"])
    with pytest.raises(CycleError, match="Circular dependency: a → b → c → d → a"):
        g.set_reads("a", ["x", "d"])
    assert g.reads("a") == ("x",)  # old reads kept
    assert g.downstream("x") == ["a", "b", "c", "d"]


def test_cycle_error_is_a_value_error() -> None:
    assert issubclass(CycleError, ValueError)
