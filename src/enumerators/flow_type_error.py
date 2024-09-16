import itertools
from copy import deepcopy
from typing import NamedTuple, Any, Tuple

import networkx as nx

from src import utils
from src.ir import ast, types as tp
from src.ir.builtins import BuiltinFactory
from src.ir.visitors import ASTExprUpdate, DefaultVisitor
from src.generators.api import nodes
from src.generators import Generator
from src.generators.api.nodes import Variable
from src.enumerators.analyses import BlockAnalysis
from src.enumerators.error import ErrorEnumerator
from src.enumerators.utils import IncompatibleTyping


class Loc(NamedTuple):
    expr: ast.Node
    parent: ast.Node
    index: int
    block_index: int


class VariableEraseType(DefaultVisitor):
    def __init__(self, variable_name: str, bt_factory,
                 use_nullable_types: bool):
        self.bt_factory = bt_factory
        self.variable_name = variable_name
        self.use_nullable_types = use_nullable_types

    def visit_var_decl(self, node):
        if node.name == self.variable_name:
            node.inferred_type = node.var_type
            if self.use_nullable_types:
                node.var_type = tp.NullableType().new([node.inferred_type])
            else:
                node.var_type = self.bt_factory.get_any_type()
                if self.bt_factory.get_language() != "kotlin":
                    node.omit_type()


class StatementInjection(DefaultVisitor):
    def __init__(self, statement: ast.Node, parent_block: ast.Block,
                 flow_variable: str, policy: str):
        self.statement = statement
        # This is the block to inject the variable
        self.parent_block = parent_block
        self.flow_variable = flow_variable
        self.policy = policy

    def visit_block(self, node: ast.Block):
        if not node.is_equal(self.parent_block):
            super().visit_block(node)
            return
        match self.policy:
            case "last":
                node.body.append(self.statement)
            case "after-decl":
                index = -1
                for i, stmt in enumerate(node.body):
                    if isinstance(stmt, ast.VariableDeclaration) and \
                            stmt.name == self.flow_variable:
                        index = i + 1
                        break
                if index != -1:
                    node.body.insert(index, self.statement)
            case _:
                raise NotImplementedError(
                    "Policy {self.policy} not supported")


def _get_source_and_prefix(source: Any, target: Any,
                           graph: nx.DiGraph) -> Tuple[Any, bool]:
    new_source = source
    remove_prefix = True
    if source == target:
        new_sources = [n for n in graph.neighbors(source)
                       if nx.has_path(graph, n, target)]
        if new_sources:
            new_source = new_sources[0]
            remove_prefix = False
    return new_source, remove_prefix


def _get_variance(type_param: tp.TypeParameter) -> tp.Variance:
    variances = [tp.Covariant, tp.Contravariant]
    if type_param.is_covariant():
        variances = [tp.Covariant]
    elif type_param.is_contravariant():
        variances = [tp.Contravariant]
    else:
        variances = [tp.Covariant, tp.Contravariant]
    return utils.random.choice(variances)


def _select_type_for_merge_var(var_type: tp.Type,
                               bt_factory: BuiltinFactory) -> tp.Type:
    supertypes = [t for t in var_type.get_supertypes()
                  if t != bt_factory.get_any_type()]
    supertype = utils.random.choice(supertypes)
    if supertype.is_parameterized():
        variants = [supertype]
        type_args = [i for i, targ in enumerate(supertype.type_args)
                     if not targ.is_wildcard()]
        s = type_args
        for subset in itertools.chain.from_iterable(
                itertools.combinations(s, r) for r in range(len(s) + 1)):
            new_type_args = list(supertype.type_args)
            for index in subset:
                type_arg = supertype.type_args[index]
                type_param = supertype.t_constructor.type_parameters[index]
                new_type_arg = tp.WildCardType(
                    bound=type_arg, variance=_get_variance(type_param))
                new_type_args[index] = new_type_arg
            variants.append(supertype.t_constructor.new(new_type_args))
        return utils.random.choice(variants)

    return supertype


class FlowBasedTypeErrorEnumerator(ErrorEnumerator):
    name = "FlowBasedTypeErrorEnumerator"
    CACHE_SIZE = 150000
    ROOT_NODE = 0

    def __init__(self, program: ast.Program, program_gen: Generator,
                 bt_factory: BuiltinFactory, options: dict = None):
        self.api_graph = getattr(program_gen, "api_graph", None)
        self.error_loc = None
        self.new_node = None
        self.analysis = BlockAnalysis()
        self.end_indices = {}
        self.location_map = {}
        super().__init__(program, program_gen, bt_factory)
        self.enumerate_all_types = False
        self.cache = set()
        self.use_nullable_types = options.get("use-nullable-types", False)

    def reset_state(self):
        self.error_loc = None
        self.new_node = None
        self.analysis = BlockAnalysis()
        self.end_indices = {}
        self.location_map = {}
        if len(self.cache) > self.CACHE_SIZE:
            self.cache = set()

    def get_candidate_program_locations(self):
        self.analysis = BlockAnalysis()
        self.analysis.visit(self.program)
        return self.to_locs(self.analysis.cfgs["test"])

    def to_locs(self, cfg: nx.DiGraph):
        locations = []
        acyclic_graph = cfg.copy()
        to_remove = [(a, b)
                     for a, b, data in acyclic_graph.edges(data=True)
                     if data.get("cycle", False)]
        acyclic_graph.remove_edges_from(to_remove)
        for node in nx.topological_sort(acyclic_graph):
            block = self.analysis.block_map.get(node)
            if block is None:
                continue
            parent, parent_index = self.analysis.get_parent_of_block(node)
            if block is None:
                continue
            end_index = self.get_block_end_index(node, cfg)
            loc = Loc(block, parent, parent_index, end_index)
            self.location_map[node] = loc
            locations.append(loc)
        return locations

    def filter_program_locations(self, locations):
        bad_locations = set()
        colored_cfg = self.color_cfg(self.analysis.cfgs["test"],
                                     self.flow_variable)
        target = self.merge_location
        for n in colored_cfg.nodes():
            is_bad = True
            if not nx.has_path(colored_cfg, n, target):
                if n in self.location_map:
                    bad_locations.add(self.location_map[n])
            source, remove_prefix = _get_source_and_prefix(
                n, target, colored_cfg)
            for path in nx.all_simple_paths(colored_cfg, source, target):
                pp = path
                if remove_prefix:
                    pp = path[1:] if path != [target] else path
                if all(not colored_cfg.nodes[p].get("green", False)
                       for p in pp):
                    is_bad = False
                    break
            if is_bad and n in self.location_map:
                bad_locations.add(self.location_map[n])

        filtered_locs = [loc for loc in locations if loc not in bad_locations]
        return filtered_locs

    def get_block_end_index(self, block_id: int, cfg: nx.DiGraph) -> int:
        acyclic_graph = cfg.copy()
        to_remove = [(a, b)
                     for a, b, data in acyclic_graph.edges(data=True)
                     if data.get("cycle", False)]
        acyclic_graph.remove_edges_from(to_remove)
        block = self.analysis.block_map[block_id]
        block_children = [self.analysis.block_map[n]
                          for n in acyclic_graph.neighbors(block_id)
                          if n in self.analysis.block_map]
        len_block = len(block.body)
        end_index = len_block
        if not block_children:
            return end_index
        for i, node in enumerate(block.body[::-1]):
            if isinstance(node, ast.Conditional):
                if node.true_branch in block_children:
                    end_index = len_block - i - 1
                    break
            if isinstance(node, ast.MultiConditional):
                if node.branches[0] in block_children:
                    end_index = len_block - i - 1
                    break
            if isinstance(node, ast.TryCatch):
                if node.try_block in block_children:
                    end_index = len_block - i - 1
                    break
            if isinstance(node, ast.Loop):
                if node.block in block_children:
                    end_index = len_block - i - 1
                    break
        return end_index

    def get_block_indices(self, block_id: int,
                          cfg: nx.DiGraph) -> Tuple[int, int]:
        block = self.analysis.block_map[block_id]
        end_index = self.get_block_end_index(block_id, cfg)
        indices = self.end_indices.get(block)
        if not indices:
            start_index = 0
            self.end_indices[block] = {end_index}
        else:
            start_index = max(indices) + 1
            self.end_indices[block].add(end_index)
        return start_index, end_index

    def color_cfg(self, cfg: nx.DiGraph, flow_variable: str) -> nx.DiGraph:
        colored_cfg = cfg.copy()
        acyclic_graph = cfg.copy()
        to_remove = [(a, b)
                     for a, b, data in acyclic_graph.edges(data=True)
                     if data.get("cycle", False)]
        acyclic_graph.remove_edges_from(to_remove)
        for block_id in nx.topological_sort(acyclic_graph):
            block = self.analysis.block_map.get(block_id)
            if block is None:
                continue
            start_index, end_index = self.get_block_indices(block_id, cfg)
            for stmt in block.body[start_index:end_index]:
                if isinstance(stmt, ast.Assignment) and \
                        stmt.name == flow_variable:
                    nx.set_node_attributes(colored_cfg, {block_id: True},
                                           name="green")

        return colored_cfg

    def get_programs_with_error(self, location):
        var_type = self.var_type
        typer = IncompatibleTyping(self.api_graph, self.bt_factory)
        if self.use_nullable_types:
            type_gen = [tp.NullableType().new([self.var_type])]
        else:
            type_gen = typer.enumerate_incompatible_typings(var_type, location)
            if not self.enumerate_all_types:
                type_gen = [next(type_gen)]
        inverse_map = {v: k for k, v in self.location_map.items()}
        for incmp_t in type_gen:
            if self.use_nullable_types:
                expr = ast.NullConstant(incmp_t)
            else:
                expr = self.program_gen._generate_expr_from_node(incmp_t).expr
            expr.mk_typed(ast.TypePair(expected=self.var_type,
                                       actual=incmp_t))
            assignment = ast.Assignment(self.flow_variable, expr)
            index = location.block_index
            if inverse_map[location] == self.merge_location and \
                    not isinstance(location.parent, ast.Loop):
                index -= 1
                pass
            new_expr = deepcopy(location.expr)
            new_expr.body.insert(index, assignment)
            upd = ASTExprUpdate(location.index, new_expr)
            upd.visit(location.parent)
            self.add_err_message(location, assignment)
            yield self.program

    def add_err_message(self, loc, new_node, *args):
        self.error_loc = loc
        self.new_node = new_node

    @property
    def error_explanation(self):
        if self.error_loc is None:
            return

        var_type = self.api_graph.get_concrete_output_type(
            nodes.Variable(self.flow_variable))
        actual_t = self.new_node.expr.get_type_info()[1]
        # Get the string representation of expressions
        translator = self.program_gen.translator
        translator.context = self.program.context
        translator.visit(self.new_node.expr)
        expr_str = translator._children_res[-1]
        translator._reset_state()
        msg = (f"Added assignment for variable {self.flow_variable}\n"
               f" - Expected type {var_type}\n"
               f" - Actual type {actual_t}\n"
               f" - New expression {expr_str}\n"
               f" - Parent block: {type(self.error_loc.parent)}\n"
               f" - Inserted error at index: {self.error_loc.block_index}\n"
               f" - Flow variable: {self.flow_variable}")
        return msg

    def enumerate_programs(self):
        flow_vars = [
            n for n in self.program_gen.api_graph.api_graph.nodes()
            if isinstance(n, Variable)
        ]
        original_program = self.program
        self.analysis.visit(self.program)
        cfg = self.analysis.cfgs["test"]
        block_map = self.analysis.block_map.copy()
        # These are the candidate locations to inject the merge variable.
        merge_locations = [
            n for n in cfg.nodes()
            if cfg.in_degree(n) != 0 and n in block_map
        ]
        for flow_variable, merge_location in itertools.product(
            flow_vars, merge_locations
        ):
            self.program = deepcopy(original_program)
            self.reset_state()
            self.analysis.visit(self.program)
            cfg = self.analysis.cfgs["test"]
            induced_nodes = nx.ancestors(cfg, merge_location)
            induced_nodes.add(merge_location)
            subg = cfg.subgraph(induced_nodes)
            if any(nx.is_isomorphic(g, subg) for g in self.cache):
                continue
            else:
                self.cache.add(subg)
            self.flow_variable = flow_variable.name
            # erase the type of the variable make it flow-sensitive.
            original_var_type = self.api_graph.get_concrete_output_type(
                flow_variable)
            VariableEraseType(
                flow_variable.name,
                self.bt_factory,
                self.use_nullable_types
            ).visit(self.program)
            var_type = _select_type_for_merge_var(original_var_type,
                                                  self.bt_factory)
            self.var_type = var_type
            merge_var_decl = ast.VariableDeclaration(
                utils.random.word(),
                ast.Variable(self.flow_variable),
                is_final=True,
                var_type=var_type
            )
            self.merge_location = merge_location
            end_index = self.get_block_end_index(merge_location, cfg)
            self.analysis.block_map[merge_location].body.insert(
                end_index, merge_var_decl)
            if self.bt_factory.get_language() in ["kotlin", "scala"]:
                assignment = ast.Assignment(
                    self.flow_variable,
                    self.program_gen.generate_expr(original_var_type)
                )
                StatementInjection(
                    assignment,
                    self.analysis.block_map[self.ROOT_NODE],
                    self.flow_variable,
                    "after-decl"
                ).visit(self.program)
            yield from super().enumerate_programs()
