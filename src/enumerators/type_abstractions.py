from typing import List, NamedTuple, Union

import networkx as nx

from src.ir import types as tp, builtins as bt


class TypeParameterAbstraction(NamedTuple):
    has_bound: bool

    def to_sign(self):
        return str(int(self.has_bound))


class SimpleClassifierAbstraction(NamedTuple):
    tree_hash: str


class TypeConstructorAbstraction(NamedTuple):
    type_parameters: List[str]
    tree_hash: str


class ParameterizedTypeAbstraction(NamedTuple):
    Signs = [
        "TP", "SC", "TC", "PT", "W"
    ]

    type_args: str
    tree_hash: str


TypeAbstraction = Union[TypeParameterAbstraction, SimpleClassifierAbstraction,
                        TypeConstructorAbstraction,
                        ParameterizedTypeAbstraction]


def get_inheritance_tree(t: tp.Type, bt_factory: bt.BuiltinFactory) -> str:
    tree = nx.DiGraph()
    tree.add_node(t.name)
    nx.set_node_attributes(tree, {t.name: get_type_sign(t)},
                           name="type")
    visited = set()
    visited.add(t.name)
    worklist = []
    worklist.append(t)
    while worklist:
        n = worklist.pop(0)
        supertypes = n.supertypes
        if len(n.supertypes) > 2:
            supertypes = {st for st in supertypes
                          if st != bt_factory.get_any_type()}
        for st in supertypes:
            if st not in tree:
                tree.add_node(st.name)
                nx.set_node_attributes(tree, {st.name: get_type_sign(st)},
                                       name="type")
                tree.add_edge(st.name, n.name)
                worklist.append(st)
    return tree


def get_type_sign(t: tp.Type) -> str:
    if t.is_type_var():
        return "TP"
    elif t.is_type_constructor():
        return "TC"
    elif t.is_parameterized():
        return "PT" + str(len(t.type_args))
    elif t.is_wildcard():
        return "T"
    else:
        return "SC"


def to_type_abstraction(t: tp.Type,
                        bt_factory: bt.BuiltinFactory) -> TypeAbstraction:
    if t.is_type_var():
        return TypeParameterAbstraction(t.bound is not None)

    tree_hash = nx.weisfeiler_lehman_graph_hash(get_inheritance_tree(
        t, bt_factory), node_attr="type")

    if t.is_parameterized():
        signs = [to_type_abstraction(type_arg,
                                     bt_factory) for type_arg in t.type_args]
        return ParameterizedTypeAbstraction(tuple(signs), tree_hash)

    if t.is_type_constructor():
        type_params = [
            TypeParameterAbstraction(type_param.bound is not None).to_sign()
            for type_param in t.type_parameters
        ]
        return TypeConstructorAbstraction(",".join(type_params), tree_hash)

    return SimpleClassifierAbstraction(tree_hash)
