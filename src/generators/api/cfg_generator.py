from collections import namedtuple, OrderedDict
from copy import deepcopy
import json
import functools
import re
from typing import List, Tuple, Union

import networkx as nx

from src import utils
from src.config import cfg
from src.enumerators import get_error_enumerator
from src.ir import ast, types as tp, type_utils as tu
from src.ir.context import Context
from src.compilers import compile_program
from src.generators.api import builder, api_graph as ag, matcher as match
from src.generators.api.api_generator import APIClientGenerator
from src.generators.api.special_methods import (
    GROOVY_SPECIAL_METHODS, KOTLIN_SPECIAL_METHODS,
    SCALA_SPECIAL_METHODS, JAVA_SPECIAL_METHODS
)
from src.modules.logging import log, log_onerror, log_error


CATCH_EXCEPTIONS = {
    "kotlin": [
        "java.lang.Exception",
        "java.io.IOException",
        "java.lang.NumberFormatException",
        "java.lang.IllegalStateException",
        "java.lang.AssertionError",
        "java.lang.ClassCastException",
        "java.lang.ArrayStoreException",
    ],
    "groovy": [
        "java.lang.Exception",
        "java.io.IOException",
        "java.lang.NumberFormatException",
        "java.lang.IllegalStateException",
        "java.lang.AssertionError",
        "java.lang.ClassCastException",
        "java.lang.ArrayStoreException",
    ],
    "scala": [
        "java.lang.Exception",
        "java.io.IOException",
        "java.lang.NumberFormatException",
        "java.lang.IllegalStateException",
        "java.lang.AssertionError",
        "java.lang.ClassCastException",
        "java.lang.ArrayStoreException",
    ],
    "java": [
        "java.lang.Exception",
        "java.io.IOException",
        "java.lang.NumberFormatException",
        "java.lang.IllegalStateException",
        "java.lang.AssertionError",
        "java.lang.ClassCastException",
        "java.lang.ArrayStoreException",
    ],
}


def get_extra_api_components(spec: dict, predicate):
    sub_spec = {}
    for k, v in spec.items():
        if predicate(v):
            sub_spec[k] = v
    return sub_spec


def get_base_api_name(fqn: str, spec: dict):
    segs = fqn.rsplit(".", 1)
    assert len(segs) == 2
    parent, base = tuple(segs)
    if parent not in spec or not spec[parent].get("is_class", True):
        # We encounter this case: mypkg.MyCls
        return base

    # Perhaps, we encounter a nested class/component.
    return get_base_api_name(parent, spec) + "." + base


def get_namespace_from_name(fqn: str, spec: dict):
    segs = fqn.rsplit(".", 1)
    assert len(segs) == 2
    parent, base = tuple(segs)
    if parent not in spec or not spec[parent].get("is_class", True):
        # We encounter this case: mypkg.MyCls
        return ast.GLOBAL_NAMESPACE
    return get_namespace_from_name(parent, spec) + (parent,)


def get_parent_classes(fqn: str, spec: dict) -> List[str]:
    segs = fqn.rsplit(".", 1)
    assert len(segs) == 2
    parent, base = tuple(segs)
    if parent not in spec or not spec[parent].get("is_class", True):
        # We encounter this case: mypkg.MyCls
        return []
    return get_parent_classes(parent, spec) + [parent]


def get_nested_classes(fqn: str, spec: dict) -> List[str]:
    return [
        k for k, v in spec.items()
        if k != fqn and k.startswith(fqn + ".") and v.get("is_class", True)
    ]


def to_namespace(m: ag.Method) -> str:
    param_str = ", ".join([str(p.t) for p in m.parameters])
    func_name = m.name
    if "." in func_name:
        func_name = func_name.rsplit(".", 1)[1]
    return f"{func_name}({param_str})"


def is_parent_interface(child_name: str, parent_name: str,
                        api_spec: dict) -> bool:
    class_type = api_spec.get(parent_name, {}).get("class_type")
    if class_type is not None:
        return class_type

    assert child_name in api_spec, "Child class specification not found"

    cls_spec = api_spec[child_name]
    return not any(sc.startswith(parent_name)
                   for sc in cls_spec["inherits"])


def is_definition_abstract(ns_spec: dict, node: Union[ag.Method, ag.Field]):
    is_parent_abstract = (
        ns_spec.get("class_type") == ast.ClassDeclaration.INTERFACE)
    is_abstract = (not node.metadata.get("static", False) and
                   not node.metadata.get("default", False) and
                   (is_parent_abstract or
                    node.metadata.get("abstract", False)))
    return is_abstract


def select_type_for_is_check(t: tp.Type, api_graph: ag.APIGraph) -> tp.Type:
    subtypes = api_graph.subtypes(t, include_parameterized_subtypes=False)
    assert len(subtypes) >= 1
    selected_t = utils.random.choice(list(subtypes))
    if selected_t.is_type_constructor():
        return selected_t.new([tp.WildCardType()
                               for _ in range(len(selected_t.type_parameters))])
    if selected_t.is_parameterized():
        return selected_t.t_constructor.new(
            [tp.WildCardType()
             for _ in range(len(selected_t.t_constructor.type_parameters))]
        )
    return selected_t


def tree2cfgtree(tree):
    graph = nx.DiGraph()
    root = 0
    graph.add_node(str(root))
    stack = [root]
    visited = set()
    while stack:
        n = stack.pop()
        if n in visited:
            continue
        visited.add(n)
        neighbors = [t for t in list(tree.neighbors(n))
                     if t > n]
        if not neighbors:
            continue
        stack.extend([t for t in neighbors if t not in visited])
        if len(neighbors) > 1:
            for t in neighbors:
                graph.add_node(str(t))
                graph.add_edge(str(n), str(t))
            leafs = [t for t in neighbors
                     if all(n < t for n in tree.neighbors(t))]
            if len(neighbors) > 2 and leafs and utils.random.bool():
                neighbor = utils.random.choice(leafs)
                nx.set_node_attributes(graph, {str(neighbor): True},
                                       name="inactive")
        else:
            if utils.random.bool():
                # We randomly decide to split the node into 2-4 nodes and
                # merge the paths that stem from these nodes to a new
                # merge node.
                random_nodes = utils.random.integer(2, 4)
                new_nodes = []
                for i in range(random_nodes):
                    t_n = f"{neighbors[0]}_{i}"
                    new_nodes.append(t_n)
                    graph.add_node(t_n)
                    graph.add_edge(str(n), t_n)
                if [t for t in tree.neighbors(neighbors[0]) if t > neighbors[0]]:
                    for s in new_nodes:
                        graph.add_edge(s, str(neighbors[0]))
            else:
                # We just created a single node. This corresponds to a loop.
                # Mark the node accordingly.
                graph.add_node(str(neighbors[0]))
                graph.add_edge(str(n), str(neighbors[0]))
                nx.set_node_attributes(graph, {str(neighbors[0]): True},
                                       name="loop")
    return graph


Namespace = namedtuple("namespace", ["api_component_name"])


class CFGGenerator(APIClientGenerator):
    API_GRAPH_BUILDERS = {
        "java": builder.JavaAPIGraphBuilder,
        "kotlin": builder.KotlinAPIGraphBuilder,
        "groovy": builder.JavaAPIGraphBuilder,
        "scala": builder.ScalaAPIGraphBuilder,
    }

    SPECIAL_METHODS = {
        "groovy": GROOVY_SPECIAL_METHODS,
        "kotlin": KOTLIN_SPECIAL_METHODS,
        "scala": SCALA_SPECIAL_METHODS,
        "java": JAVA_SPECIAL_METHODS,
    }

    def __init__(self, api_docs, options={}, language=None,
                 logger=None):
        super().__init__(api_docs, options=options, language=language,
                         logger=logger)
        self.options = options
        api_docs.update(self.SPECIAL_METHODS[self.bt_factory.get_language()])
        self.api_docs = api_docs
        self.initial_api_graph = self.api_graph
        self.package_name = None
        self.programs_gen = self.compute_programs()
        self.namespace = ast.GLOBAL_NAMESPACE
        self.block_variables = True
        api_rules_file = options.get("api-rules")
        api_namespaces = list(api_docs.keys())
        if api_rules_file:
            matcher = match.parse_rule_file(api_rules_file)
            api_namespaces = [k for k in api_namespaces
                              if matcher.match(Namespace(k))]
        self.api_namespaces = utils.random.shuffle(api_namespaces)
        self.ErrorEnumerator = get_error_enumerator(
            self.options.get("error-enumerator"))
        self.max_local_vars = options.get("max-local-vars", 5)
        self.max_cfg_nodes = options.get("max-cfg-nodes")

    def _fork_api_spec(self, specs: Tuple[str, dict],
                       selected_namespaces: List[str],
                       replace_fqn: bool = True) -> dict:
        forked_specs = {}
        for name, spec in specs:
            # The new name of the component (e.g., class) is derived as follows
            # <pkg_name>.<base_name>
            # where <pkg_name> stands for the package of the generated program
            # where <base_name> represents the base name of the component
            # as taken from the input API.
            new_name = self.package_name + "." + get_base_api_name(
                name, self.api_docs)
            spec_str = json.dumps(spec)
            if replace_fqn:
                spec_str = functools.reduce(lambda acc, x: re.sub(
                    re.compile(x.replace(".", "\\.") + "(?![A-Za-z.])"),
                    self.package_name + "." + get_base_api_name(x, self.api_docs),
                    acc), selected_namespaces, spec_str)
                new_spec = json.loads(spec_str)
            else:
                new_spec = spec
            forked_specs[new_name] = new_spec
        return forked_specs

    def _handle_nested_classes(self, specs: List[dict],
                               included_ns: List[str]) -> set:
        included_libs = set()
        for cls_name, spec in list(specs):
            # If the current namespace has parent classes, include these
            # classes to our list of specs.
            for parent in get_parent_classes(cls_name, self.api_docs):
                if parent not in included_ns:
                    specs.append((parent, self.api_docs[parent]))
                    included_ns.append(parent)
            # Now handle any nested classes defined in the class given
            # by `cls_name`.
            for nested_c in get_nested_classes(cls_name, self.api_docs):
                if nested_c not in included_ns:
                    specs.append((nested_c, self.api_docs[nested_c]))
                    included_ns.append(nested_c)
                    t = (self.initial_api_graph.get_type_by_name(nested_c) or
                         self.parse_builtin_type(nested_c))
                    # Get the supertypes of ns
                    included_libs.update({
                        st.name for st in t.get_supertypes()
                        if st != self.bt_factory.get_any_type()})
        return included_libs

    def _add_missing_api_specs(self, forked_spec: dict):
        for k, v in self.api_docs.items():
            if k in forked_spec:
                continue
            copied_v = deepcopy(v)
            # For performance reasons, we don't include the specification
            # of the included methods.
            copied_v["methods"] = [m for m in copied_v["methods"]
                                   if m["is_constructor"]]
            copied_v["fields"] = []
            forked_spec[k] = copied_v

    def fork_api_spec(self, ns: str, replace_fqn: bool) -> dict:
        """
        Given a namespace (e.g., a class name), we find all the namespaces
        (e.g., classes) related to that (e.g., parent-child relationships).
        Then, we fork parts of the given API by changing the names of the
        given namespace `ns` and the related namespaces.
        """
        # We might encounter a namespace of the form: java.lang.Integer
        t = (self.initial_api_graph.get_type_by_name(ns) or
             self.parse_builtin_type(ns))
        # Get the supertypes of ns
        supertypes = {
            st for st in t.get_supertypes()
            if st != self.bt_factory.get_any_type()}
        # Note that we omit parent classes not defined in the given API.
        # We do that because their specification is not available to us.
        specs = [(ns, self.api_docs[ns])]
        specs.extend([(st.name, self.api_docs[st.name]) for st in supertypes
                     if st.name != ns and st.name in self.api_docs])
        all_names = [s[0] for s in specs]

        # Now select a random subset of namespaces. This means that our class
        # will inherit from some user-defined classes, but also inherit from
        # the classes that come from the given API.
        selected_namespaces = utils.random.sample(
            all_names, k=utils.random.integer(1, len(all_names)))
        if ns not in selected_namespaces:
            selected_namespaces.append(ns)
        specs = [s for s in specs if s[0] in selected_namespaces]
        included_libs = self._handle_nested_classes(specs, selected_namespaces)
        included_libs.update(all_names)
        forked_spec = self._fork_api_spec(specs, selected_namespaces,
                                          replace_fqn)
        keys = forked_spec.keys()
        for elem in included_libs:
            if elem not in keys and elem in self.api_docs:
                forked_spec[elem] = self.api_docs[elem]
        self._add_missing_api_specs(forked_spec)
        return forked_spec

    def generate_expr_from_special_method(self, m: ag.Method,
                                          depth: int,
                                          type_var_map: dict) -> ast.Expr:
        parameters = [param.t for param in m.parameters]
        args = self._generate_args(parameters,
                                   [[p] for p in parameters],
                                   depth + 1, type_var_map)
        catch_exceptions = CATCH_EXCEPTIONS.get(
            self.bt_factory.get_language(), [])
        converters = {
            "!": lambda args: ast.UnaryExpr(args[0].expr,
                                            ast.Operator("!"),
                                            is_prefix=True),
            "&&": lambda args: ast.LogicalExpr(args[0].expr, args[1].expr,
                                               ast.Operator("&&")),
            "||": lambda args: ast.LogicalExpr(args[0].expr, args[1].expr,
                                               ast.Operator("||")),
            "==": lambda args: ast.EqualityExpr(args[0].expr, args[1].expr,
                                                ast.Operator("==")),
            "!=": lambda args: ast.EqualityExpr(args[0].expr, args[1].expr,
                                                ast.Operator("=", is_not=True)),
            "+": lambda args: ast.ArithExpr(args[0].expr, args[1].expr,
                                            ast.Operator("+")),
            "-": lambda args: ast.ArithExpr(args[0].expr, args[1].expr,
                                            ast.Operator("-")),
            "/": lambda args: ast.ArithExpr(args[0].expr, args[1].expr,
                                            ast.Operator("/")),
            "*": lambda args: ast.ArithExpr(args[0].expr, args[1].expr,
                                            ast.Operator("*")),
            ">": lambda args: ast.ComparisonExpr(args[0].expr, args[1].expr,
                                                 ast.Operator(">")),
            "<": lambda args: ast.ComparisonExpr(args[0].expr, args[1].expr,
                                                 ast.Operator(">")),
            ">=": lambda args: ast.ComparisonExpr(args[0].expr, args[1].expr,
                                                  ast.Operator(">=")),
            "<=": lambda args: ast.ComparisonExpr(args[0].expr, args[1].expr,
                                                  ast.Operator("<=")),
            "?:": lambda args: ast.BinaryExpr(args[0].expr, args[1].expr,
                                              ast.Operator("?:")),
            "[]": lambda args: ast.BinaryExpr(args[0].expr, args[1].expr,
                                              ast.Operator("[]", wrap=True)),
            "!!": lambda args: ast.UnaryExpr(args[0].expr,
                                             ast.Operator("!!"),
                                             is_prefix=False),
            "_if_": lambda args: ast.Conditional(
                args[0].expr, args[1].expr, args[2].expr,
                inferred_type=(args[1].path[-1]
                               if args[2].path[-1].is_subtype(args[1].path[-1])
                               else args[2].path[-1])
            ),
            "_when_": lambda args: ast.MultiConditional(
                [a.expr for a in args[1:m.metadata["conditions"] + 1]],
                [a.expr for a in args[m.metadata["conditions"] + 1:]],
                inferred_type=functools.reduce(
                    lambda acc, x: acc if x.is_subtype(acc) else x,
                    [a.path[-1] for a in args[m.metadata["conditions"] + 1:]],
                    args[m.metadata["conditions"] + 1].path[-1]
                ),
                root_cond=args[0].expr,
                is_expression=True
            ),
            "_try_": lambda args: ast.TryCatch(
                ast.Block([args[0].expr], False),
                OrderedDict(
                    (catch_exceptions[i], ast.Block([arg.expr], False))
                    for i, arg in enumerate(args[1:])
                )
            ),
            "_is_": lambda args: ast.Is(
                args[0].expr, select_type_for_is_check(
                    type_var_map[m.type_parameters[0]],
                    self.api_graph
                )
            )
        }
        symbol = m.metadata["symbol"]
        expr = converters[symbol](args)
        out_type = self.api_graph.get_concrete_output_type(m)
        out_type = tp.substitute_type(out_type, type_var_map)
        return expr

    def _generate_expression_from_path(self, path: list, depth: int,
                                       type_var_map: dict,
                                       original_path: list) -> ast.Expr:

        elem = path[-1]
        if isinstance(elem, ag.Method) and elem.metadata.get("is_special"):
            return self.generate_expr_from_special_method(elem, depth,
                                                          type_var_map)
        else:
            return super()._generate_expression_from_path(path, depth,
                                                          type_var_map,
                                                          original_path)

    def generate_cfg_tree(self) -> nx.Graph:
        while True:
            nu_nodes = utils.random.integer(min_int=5,
                                            max_int=self.max_cfg_nodes)
            yield tree2cfgtree(nx.random_unlabeled_rooted_tree(nu_nodes))

    def create_local_vars(self):
        types = self.api_graph.get_reg_types()
        candidate_types = [
            t for t in types
            if t.is_type_constructor() or len(t.get_supertypes()) > 3
        ]
        if not candidate_types:
            candidate_types = types
        local_vars = []
        ref_type = utils.random.choice(types)
        type_pool = {t for t in ref_type.get_supertypes()
                     if t != self.bt_factory.get_any_type()}
        type_pool = list(type_pool)
        for i in range(utils.random.integer(1, self.max_local_vars)):
            var_type = utils.random.choice(type_pool)
            if var_type.is_type_constructor():
                var_type = tu.instantiate_type_constructor(
                    var_type, candidate_types)
                if var_type is None:
                    continue
                var_type = var_type[0]
            var_name = utils.random.word()
            expr = self.generate_expr(var_type)
            var_decl = ast.VariableDeclaration(var_name, expr,
                                               is_final=False,
                                               var_type=var_type)
            self.context.add_var(self.namespace, var_decl.name, var_decl)
            self.api_graph.add_variable_node(var_decl.name, var_type)
            local_vars.append(var_decl)
        return local_vars

    def create_assignments(self, local_vars):
        if not local_vars:
            return []
        max_assignments = len(local_vars)
        assignments = []
        var_pool = list(local_vars)
        for i in range(utils.random.integer(0, max_assignments)):
            local_var = utils.random.choice(var_pool)
            var_pool.remove(local_var)
            out_type = local_var.get_type()
            kwargs = {
                "name": local_var.name,
                "receiver": None,
            }
            subtypes = self.api_graph.subtypes(
                out_type,
                include_parameterized_subtypes=False
            )
            var_type = utils.random.choice(list(subtypes))
            if var_type.is_type_constructor():
                var_type, _ = tu.instantiate_type_constructor(
                    var_type, self.api_graph.get_reg_types())
            expr = self._generate_expr_from_node(var_type).expr
            kwargs["expr"] = expr
            assignments.append(ast.Assignment(**kwargs))
        return assignments

    def create_conditional(self, children_blocks: List[ast.Block],
                           nu_edges: int) -> ast.Conditional:
        return ast.Conditional(
            self.generate_expr(self.bt_factory.get_boolean_type()),
            children_blocks[0],
            children_blocks[1],
            self.bt_factory.get_void_type(),
            is_expression=False
        )

    def create_multiconditional(self, children_blocks: List[ast.Block],
                                nu_edges: int) -> ast.MultiConditional:
        nu_cases = (
            nu_edges - 1
            if len(children_blocks) == nu_edges
            else len(children_blocks)
        )
        cond_type = self.bt_factory.get_integer_type()
        return ast.MultiConditional(
            [self.generate_expr(cond_type) for _ in range(nu_cases)],
            children_blocks,
            self.bt_factory.get_void_type(),
            self.generate_expr(cond_type),
            is_expression=False
        )

    def create_trycatch(self, children_blocks: List[ast.Block],
                        nu_edges: int) -> ast.TryCatch:
        catch_exceptions = CATCH_EXCEPTIONS[self.bt_factory.get_language()]
        assert nu_edges > 2
        return ast.TryCatch(
            children_blocks[0],
            {catch_exceptions[i]: children_blocks[i + 1]
             for i in range(nu_edges - 2)}
        )

    def create_loop(self, children_blocks: List[ast.Block],
                    nu_edges: int) -> ast.Loop:
        assert len(children_blocks) == 1
        return ast.Loop(
            children_blocks[0],
            loop_type=utils.random.choice([
                ast.Loop.WHILE_LOOP,
                ast.Loop.FOR_LOOP,
            ])
        )

    def generate_program_from_cfg_tree(self, tree: nx.Graph) -> ast.Program:
        self.context = Context()
        self.namespace += ("test",)
        local_vars = self.create_local_vars()
        assignments = self.create_assignments(local_vars)
        root_node = "0"
        block = ast.Block(local_vars + assignments)
        blocks = {}
        stack = [(root_node, block)]
        blocks[root_node] = block
        visited = set()
        while stack:
            n, block = stack.pop()
            if n in visited:
                continue
            visited.add(n)
            neighbors = list(tree.neighbors(n))
            nu_neighbors = len(neighbors)
            children_blocks = []
            if nu_neighbors > 1:
                for i, neighbor in enumerate(neighbors):
                    if not tree.nodes[neighbor].get("inactive", False):
                        assignments = self.create_assignments(local_vars)
                        children_block = ast.Block(assignments)
                        children_blocks.append(children_block)
                        stack.append((neighbor, children_block))
                        blocks[neighbor] = children_block
                if nu_neighbors == 2:
                    methods = [self.create_conditional,
                               self.create_multiconditional]
                    cond = utils.random.choice(methods)(children_blocks,
                                                        nu_neighbors)
                else:
                    methods = [self.create_multiconditional,
                               self.create_trycatch]
                    cond = utils.random.choice(methods)(children_blocks,
                                                        nu_neighbors)
                block.body.append(cond)
            elif nu_neighbors == 1:
                if tree.neighbors(neighbors[0]):
                    parents = list(tree.predecessors(n))
                    if not parents:
                        continue
                    if not tree.nodes[neighbors[0]].get("loop", False):
                        parent = parents[0]
                        block = blocks[parent]
                        stack.append((neighbors[0], block))
                        blocks[neighbors[0]] = block
                    else:
                        # This is a single node that represents a loop.
                        assignments = self.create_assignments(local_vars)
                        children_block = ast.Block(assignments)
                        stack.append((neighbors[0], children_block))
                        blocks[neighbors[0]] = children_block
                        loop = self.create_loop([children_block], 1)
                        block.body.append(loop)
            else:
                pass
        root_block = blocks[root_node]
        func_decl = ast.FunctionDeclaration(
            "test", [], self.bt_factory.get_void_type(), root_block,
            ast.FunctionDeclaration.FUNCTION)
        self.context.add_func(ast.GLOBAL_NAMESPACE, func_decl.name,
                              func_decl)
        return ast.Program(self.context, self.bt_factory.get_language())

    @log_onerror
    def generate_well_typed_program(self, tree: nx.DiGraph, api_namespace: str,
                                    program_id: int) -> ast.Program:
        """
        Generates a well-typed program from the given API namespace.
        """
        forked_spec = self.fork_api_spec(api_namespace, False)
        forked_spec.update(get_extra_api_components(
            self.api_docs, lambda x: x.get("functional_interface", False)))
        api_builder = self.API_GRAPH_BUILDERS[self.language](
            self.language, **self.options)
        api_builder.parsed_types = self.api_builder.parsed_types
        self.api_graph = api_builder.build(forked_spec)
        if self.type_eraser is not None:
            self.type_eraser.api_graph = self.api_graph
        program = self.generate_program_from_cfg_tree(tree)
        return program

    def generate_ill_typed_programs(self, program: ast.Program,
                                    program_id: int,
                                    api_namespace: str):
        """
        Generates all ill-typed programs that stem from the given well-typed
        one.
        """
        # We first attempt to compile the program. The program is expected
        # to compile. If this is not the case, then there's no need
        # to proceed with error enumeration.
        (succeeded, err), compiler = compile_program(
            self.bt_factory.get_language(), program,
            self.package_name,
            library_path=self.options.get("library-path"))

        compiler.analyze_compiler_output(err)
        if not succeeded and not compiler.crash_msg:
            log(self.logger,
                f"Skeleton program {program_id} unexpectedly does not compile")
            return None
        if compiler.crash_msg:
            log(self.logger,
                f"We found a crash with the skeleton program {program_id}")
            yield program
            return
        error_enum = self.ErrorEnumerator(program, self,
                                          self.bt_factory)
        flag = False
        try:
            cfg.substitute_wildcards = False
            for j, p in enumerate(error_enum.enumerate_programs()):
                if p is not None:
                    flag = True
                    self.error_injected = error_enum.error_explanation
                    msg = (f"Enumerating error program {j + 1}"
                           f" for skeleton {program_id}\n")
                    log(self.logger, msg)
                    log(self.logger, f"API namespace: {api_namespace}")
                    log(self.logger, self.error_injected)
                    yield p
            if not flag:
                msg = f"No error added to skeleton {program_id}"
                log(self.logger, msg)
            cfg.substitute_wildcards = True
        except Exception as exc:
            log_error(self.logger, exc)
            cfg.substitute_wildcards = True

    def compute_programs(self) -> ast.Program:
        for i, tree in enumerate(self.generate_cfg_tree()):
            program_id = i + 1
            api_namespace = utils.random.choice(self.api_namespaces)
            program = self.generate_well_typed_program(tree, api_namespace,
                                                       program_id)

            if program is None:
                continue
            if not self.ErrorEnumerator:
                yield program  # This is a well-typed program
            else:
                # Enumerate all ill-typed programs that stem from the given
                # skeleton program.
                yield from self.generate_ill_typed_programs(program,
                                                            program_id,
                                                            api_namespace)

    def has_next(self) -> bool:
        return self._has_next

    def prepare_next_program(self, program_id, package_name):
        self.context = Context()
        self.error_injected = None
        self.package_name = package_name
