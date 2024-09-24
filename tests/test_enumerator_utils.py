import json
import os

import pytest

from src.ir import types as tp, java_types as jt, ast
from src.generators.api.builder import JavaAPIGraphBuilder
from src.enumerators.utils import IncompatibleTyping, NullIncompatibleTyping
from src.enumerators.analyses import Loc


def parse_docs(doc_path: str):
    docs = {}
    for api_path in os.listdir(doc_path):
        with open(os.path.join(doc_path, api_path)) as f:
            docs[api_path.replace(".json", "")] = json.load(f)
    return docs


def to_expr(t: tp.Type) -> ast.Expr:
    expr = ast.BottomConstant(t)
    expr.mk_typed(ast.TypePair(actual=t, expected=t))
    return expr


@pytest.fixture(scope="session")
def api_graph():
    docs = parse_docs("example-apis/java-stdlib/json-docs")
    builder = JavaAPIGraphBuilder("java")
    api_graph = builder.build(docs)
    return api_graph


def test_null_type_enumeration_regular(api_graph):
    exp_t = api_graph.get_type_by_name("java.lang.CharSequence")
    cand_t = api_graph.get_type_by_name("java.util.Calendar")

    typer = NullIncompatibleTyping(api_graph, jt.JavaBuiltinFactory())
    expr = to_expr(exp_t)
    loc = Loc(expr, expr, 0, 0, {})

    assert list(typer.get_incompatible_type(cand_t, exp_t, loc)) == []
    assert list(typer.get_incompatible_type(exp_t, exp_t, loc)) == [
        tp.NullableType().new([exp_t])
    ]

    cand_t = exp_t
    exp_t = tp.NullableType().new([exp_t])
    assert list(typer.get_incompatible_type(cand_t, exp_t, loc)) == []

    exp_t = api_graph.get_type_by_name("java.lang.Cloneable")
    cand_t = api_graph.get_type_by_name("java.util.TreeMap")
    types = list(typer.get_incompatible_type(cand_t, exp_t, loc))
    assert len(types) == 1
    assert types[0].is_parameterized()
    assert isinstance(types[0].t_constructor, tp.NullableType)
    assert types[0].type_args[0].is_parameterized()
    assert isinstance(types[0].type_args[0].t_constructor, type(cand_t))


def test_null_type_enumeration_primitive(api_graph):
    exp_t = jt.IntegerType(primitive=True)
    cand_t = jt.IntegerType(primitive=False)
    typer = NullIncompatibleTyping(api_graph, jt.JavaBuiltinFactory())
    expr = to_expr(exp_t)
    loc = Loc(expr, expr, 0, 0, {})

    assert list(typer.get_incompatible_type(cand_t, exp_t, loc)) == [
        tp.NullableType().new([cand_t])
    ]


def test_null_type_enumeration_polymorphic(api_graph):
    list_t = api_graph.get_type_by_name("java.util.List")
    linkedlist_t = api_graph.get_type_by_name("java.util.LinkedList")
    list_int = list_t.new([jt.Integer])
    expr = to_expr(list_int)
    loc = Loc(expr, expr, 0, 0, {})

    typer = NullIncompatibleTyping(api_graph, jt.JavaBuiltinFactory())
    assert list(typer.get_incompatible_type(linkedlist_t, list_int, loc)) == [
        linkedlist_t.new([tp.NullableType().new([jt.Integer])]),
        tp.NullableType().new([
            linkedlist_t.new([tp.NullableType().new([jt.Integer])])

        ])
    ]

    exp_t = list_t.new([tp.WildCardType(jt.Integer, tp.Covariant)])
    assert list(typer.get_incompatible_type(linkedlist_t, exp_t, loc)) == [
        linkedlist_t.new([tp.WildCardType(tp.NullableType().new([jt.Integer]),
                                          tp.Covariant)]),
        tp.NullableType().new([
            linkedlist_t.new([tp.WildCardType(tp.NullableType().new([jt.Integer]),
                                              tp.Covariant)])
        ]),
        linkedlist_t.new([tp.NullableType().new([jt.Integer])]),
        tp.NullableType().new([
            linkedlist_t.new([tp.NullableType().new([jt.Integer])])
        ])
    ]

    exp_t = list_t.new([jt.Number])
    assert list(typer.get_incompatible_type(linkedlist_t, exp_t, loc)) == [
        linkedlist_t.new([tp.NullableType().new([jt.Number])]),
        tp.NullableType().new([
            linkedlist_t.new([tp.NullableType().new([jt.Number])])
        ])
    ]

    exp_t = list_t.new([tp.WildCardType(jt.Number, tp.Covariant)])
    types = list(typer.get_incompatible_type(linkedlist_t, exp_t, loc))

    assert types[:-2] == [
        linkedlist_t.new([tp.WildCardType(tp.NullableType().new([jt.Number]),
                                          tp.Covariant)]),
        tp.NullableType().new([
            linkedlist_t.new([tp.WildCardType(tp.NullableType().new([jt.Number]),
                                              tp.Covariant)])
        ]),
        linkedlist_t.new([tp.NullableType().new([jt.Number])]),
        tp.NullableType().new([
            linkedlist_t.new([tp.NullableType().new([jt.Number])])

        ])
    ]
    assert types[-2] in [
        linkedlist_t.new([tp.NullableType().new([jt.Byte])]),
        linkedlist_t.new([tp.NullableType().new([jt.Short])]),
        linkedlist_t.new([tp.NullableType().new([jt.Integer])]),
        linkedlist_t.new([tp.NullableType().new([jt.Long])]),
        linkedlist_t.new([tp.NullableType().new([jt.Float])]),
        linkedlist_t.new([tp.NullableType().new([jt.Double])]),
    ]

    exp_t = list_t.new([tp.NullableType().new([jt.Integer])])
    assert list(typer.get_incompatible_type(linkedlist_t, exp_t, loc)) == [
        linkedlist_t.new([jt.Integer]),
        tp.NullableType().new([
            linkedlist_t.new([jt.Integer])
        ])
    ]

    exp_t = list_t.new([tp.WildCardType(tp.NullableType().new([jt.Integer]),
                                        tp.Covariant)])
    assert list(typer.get_incompatible_type(linkedlist_t, exp_t, loc)) == []

    exp_t = list_t.new([tp.WildCardType(tp.NullableType().new([jt.Integer]),
                                        tp.Contravariant)])
    assert list(typer.get_incompatible_type(linkedlist_t, exp_t, loc)) == [
        linkedlist_t.new([tp.WildCardType(jt.Integer, tp.Contravariant)]),
        tp.NullableType().new([
            linkedlist_t.new([tp.WildCardType(jt.Integer, tp.Contravariant)])
        ]),
        linkedlist_t.new([jt.Integer]),
        tp.NullableType().new([linkedlist_t.new([jt.Integer])]),
        linkedlist_t.new([jt.Object]),
        tp.NullableType().new([
            linkedlist_t.new([jt.Object])
        ])
    ]

    exp_t = list_t.new([tp.WildCardType(jt.Integer, tp.Contravariant)])
    assert list(typer.get_incompatible_type(linkedlist_t, exp_t, loc)) == []

    exp_t = jt.Array.new([tp.NullableType().new([jt.String])])
    assert list(typer.get_incompatible_type(jt.Array, exp_t, loc)) == []

    exp_t = jt.Array.new([jt.Number])
    types = list(typer.get_incompatible_type(jt.Array, exp_t, loc))
    assert types[0] == jt.Array.new([tp.NullableType().new([jt.Number])])
    assert types[1] == tp.NullableType().new(
        [jt.Array.new([tp.NullableType().new([jt.Number])])])
    assert types[2] in [
        jt.Array.new([tp.NullableType().new([jt.Byte])]),
        jt.Array.new([tp.NullableType().new([jt.Short])]),
        jt.Array.new([tp.NullableType().new([jt.Integer])]),
        jt.Array.new([tp.NullableType().new([jt.Long])]),
        jt.Array.new([tp.NullableType().new([jt.Float])]),
        jt.Array.new([tp.NullableType().new([jt.Double])]),
    ]


def test_null_type_enumeration_nullable_polymorphic(api_graph):
    list_t = api_graph.get_type_by_name("java.util.List")
    list_int = list_t.new([jt.Integer])
    exp_t = tp.NullableType().new([list_int])
    expr = to_expr(exp_t)
    loc = Loc(expr, expr, 0, 0, {})

    typer = NullIncompatibleTyping(api_graph, jt.JavaBuiltinFactory())
    assert list(typer.get_incompatible_type(list_t, exp_t, loc)) == [
        list_t.new([tp.NullableType().new([jt.Integer])]),
        tp.NullableType().new([
            list_t.new([tp.NullableType().new([jt.Integer])]),
        ])
    ]

    # List<Int?>?
    exp_t = tp.NullableType().new([list_t.new([
        tp.NullableType().new([jt.Integer])])])
    assert list(typer.get_incompatible_type(list_t, exp_t, loc)) == [
        list_t.new([jt.Integer]),
        tp.NullableType().new([
            list_t.new([jt.Integer]),

        ])
    ]

    # List<? extends Int?>?
    exp_t = tp.NullableType().new([
        list_t.new([
            tp.WildCardType(
                tp.NullableType().new([jt.Integer]),
                tp.Covariant
            )
        ])
    ])
    assert list(typer.get_incompatible_type(list_t, exp_t, loc)) == []
