import itertools
from copy import deepcopy
from typing import NamedTuple

import networkx as nx

from src import utils
from src.ir import ast, types as tp
from src.ir.visitors import ASTExprUpdate, DefaultVisitor
from src.generators.api import nodes
from src.ir.builtins import BuiltinFactory
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
    def __init__(self, variable_name: str, bt_factory):
        self.bt_factory = bt_factory
        self.variable_name = variable_name

    def visit_var_decl(self, node):
        if node.name == self.variable_name:
            node.inferred_type = node.var_type
            node.var_type = self.bt_factory.get_any_type()
            if self.bt_factory.get_language() != "kotlin":
                node.omit_type()


class StatementInjection(DefaultVisitor):
    def __init__(self, statement, flow_variable: str, policy: str):
        self.statement = statement
        self.flow_variable = flow_variable
        self.policy = policy

    def visit_func_decl(self, node):
        if isinstance(node.body, ast.Block) and any(
            isinstance(n, ast.VariableDeclaration) and n.name == self.flow_variable
                for n in node.body.body):
            match self.policy:
                case "last":
                    node.body.body.append(self.statement)
                case "after-decl":
                    index = 0
                    for i, stmt in enumerate(node.body.body):
                        if isinstance(stmt, ast.VariableDeclaration) and \
                                stmt.name == self.flow_variable:
                            index = i + 1
                            break
                    assert index != 0
                    node.body.body.insert(index, self.statement)
                case _:
                    raise NotImplementedError(
                        "Policy {self.policy} not supported")


class FlowBasedTypeErrorEnumerator(ErrorEnumerator):
    name = "FlowBasedTypeErrorEnumerator"

    def __init__(self, program: ast.Program, program_gen: Generator,
                 bt_factory: BuiltinFactory):
        self.api_graph = program_gen.api_graph
        self.error_loc = None
        self.new_node = None
        self.analysis = BlockAnalysis()
        self.end_indices = {}
        self.location_map = {}
        super().__init__(program, program_gen, bt_factory)
        self.enumerate_all_types = False

    def reset_state(self):
        self.error_loc = None
        self.new_node = None
        self.analysis = BlockAnalysis()
        self.end_indices = {}
        self.location_map = {}

    def get_candidate_program_locations(self):
        self.analysis.visit_program(self.program)
        return self.to_locs(self.analysis.cfgs["test"])

    def to_locs(self, cfg: nx.DiGraph):
        locations = []
        for node in nx.topological_sort(cfg):
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
        leaf_nodes = [n for n in colored_cfg.nodes()
                      if colored_cfg.out_degree(n) == 0]
        assert len(leaf_nodes) == 1

        leaf_node = leaf_nodes[0]
        for n in colored_cfg.nodes():
            is_bad = True
            for path in nx.all_simple_paths(colored_cfg, n, leaf_node):
                if all(not colored_cfg.nodes[p].get("green", False)
                       for p in path[1:]):
                    is_bad = False
                    break
            if is_bad and n in self.location_map:
                bad_locations.add(self.location_map[n])
        filtered_locs = [loc for loc in locations if loc not in bad_locations]
        return filtered_locs

    def get_block_end_index(self, block_id, cfg):
        block = self.analysis.block_map[block_id]
        block_children = [self.analysis.block_map[n]
                          for n in cfg.neighbors(block_id)
                          if n in self.analysis.block_map]
        len_block = len(block.body)
        end_index = len_block
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
        if not block_children:
            end_index -= 1
        return end_index

    def get_block_indices(self, block_id, cfg):
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
        for block_id in nx.topological_sort(cfg):
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
        type_gen = typer.enumerate_incompatible_typings(var_type, location)
        if not self.enumerate_all_types:
            type_gen = [next(type_gen)]
        for incmp_t in type_gen:
            assignment = ast.Assignment(
                self.flow_variable,
                self.program_gen._generate_expr_from_node(incmp_t).expr
            )
            new_expr = deepcopy(location.expr)
            new_expr.body.insert(location.block_index, assignment)
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
        # Get the string representation of expressions
        translator = self.program_gen.translator
        translator.context = self.program.context
        translator.visit(self.new_node.expr)
        expr_str = translator._children_res[-1]
        translator._reset_state()
        msg = (f"Added assignment for variable {self.flow_variable}\n"
               f" - Expected type {var_type}\n"
               f" - New expression {expr_str}\n"
               f" - Parent block: {type(self.error_loc.parent)}\n"
               f" - Inserted error at index: {self.error_loc.block_index}\n"
               f" - Flow variable: {self.flow_variable}")
        return msg

    def select_type_for_merge_var(self, var_type: tp.Type) -> tp.Type:
        supertypes = [t for t in var_type.get_supertypes()
                      if t != self.bt_factory.get_any_type()]
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
                    new_type_arg = tp.WildCardType(
                        bound=type_arg, variance=tp.Covariant)
                    new_type_args[index] = new_type_arg
                variants.append(supertype.t_constructor.new(new_type_args))
            return utils.random.choice(variants)

        return supertype

    def enumerate_programs(self):
        flow_vars = [
            n for n in self.program_gen.api_graph.api_graph.nodes()
            if isinstance(n, Variable)
        ]
        original_program = self.program
        for flow_variable in flow_vars:
            self.program = deepcopy(original_program)
            self.reset_state()
            self.flow_variable = flow_variable.name
            VariableEraseType(flow_variable.name, self.bt_factory).visit(
                self.program
            )
            var_type = self.api_graph.get_concrete_output_type(flow_variable)
            var_type = self.select_type_for_merge_var(var_type)
            if not var_type.is_parameterized():
                break
            self.var_type = var_type
            merge_var_decl = ast.VariableDeclaration(
                utils.random.word(),
                ast.Variable(self.flow_variable),
                is_final=True,
                var_type=var_type
            )
            # Insert merge variable declaration at the end of block.
            StatementInjection(
                merge_var_decl, self.flow_variable, "last"
            ).visit(self.program)
            if self.bt_factory.get_language() == "kotlin":
                assignment = ast.Assignment(
                    self.flow_variable,
                    self.program_gen._generate_expr_from_node(var_type).expr
                )
                StatementInjection(
                    assignment,
                    self.flow_variable,
                    "after-decl"
                ).visit(self.program)
            yield from super().enumerate_programs()
