from typing import NamedTuple

from src.enumerators.error import ErrorEnumerator
from src.ir import ast
from src.ir.builtins import BuiltinFactory
from src.generators.api import api_graph as ag


class TypeErrorEnumerator(ErrorEnumerator):

    class Loc(NamedTuple):
        expr: ast.Expr
        parent: ast.Node
        index: int

    def __init__(self, program: ast.Program, api_graph: ag.APIGraph,
                 bt_factory: BuiltinFactory):
        super().__init__(program, api_graph, bt_factory)
        self.locations = []

    def visit_block(self, node):
        super().visit_block(node)
        for i, elem in enumerate(node.body):
            if isinstance(elem, ast.Expr):
                self.locations.append(self.Loc(elem, node, i))

    def visit_var_decl(self, node):
        super().visit_var_decl(node)
        self.locations.append(self.Loc(node.expr, node, 0))

    def visit_func_decl(self, node):
        super().visit_func_decl(node)
        if node.body and isinstance(node.body, ast.Expr):
            self.locations.append(self.Loc(node.body, node, 0))

    def visit_lambda(self, node):
        super().visit_lambda(node)
        if isinstance(node.body, ast.Expr):
            self.locations.append(self.Loc(node.body, node, 0))

    def visit_func_ref(self, node):
        super().visit_func_ref(node)
        if node.receiver:
            self.locations.append(self.Loc(node.receiver, node, 0))

    def visit_array_expr(self, node):
        super().visit_array_expr(node)
        for i, expr in enumerate(node.exprs):
            self.locations.append(self.Loc(expr, node, i))

    def visit_logical_expr(self, node):
        super().visit_logical_expr(node)
        self.locations.append(self.Loc(node.lexpr, node, 0))
        self.locations.append(self.Loc(node.rexpr, node, 1))

    def visit_equality_expr(self, node):
        super().visit_equality_expr(node)
        self.locations.append(self.Loc(node.lexpr, node, 0))
        self.locations.append(self.Loc(node.rexpr, node, 1))

    def visit_comparison_expr(self, node):
        super().visit_comparison_expr(node)
        self.locations.append(self.Loc(node.lexpr, node, 0))
        self.locations.append(self.Loc(node.rexpr, node, 1))

    def visit_arith_expr(self, node):
        super().visit_arith_expr(node)
        self.locations.append(self.Loc(node.lexpr, node, 0))
        self.locations.append(self.Loc(node.rexpr, node, 1))

    def visit_conditional(self, node):
        super().visit_conditional(node)
        self.locations.append(self.Loc(node.cond, node, 0))
        if isinstance(node.true_branch, ast.Expr):
            self.locations.append(self.Loc(node.true_branch, node, 1))
        if isinstance(node.false_branch, ast.Expr):
            self.locations.append(self.Loc(node.false_branch, node, 1))

    def visit_new(self, node):
        super().visit_new(node)
        for i, e in enumerate(node.args):
            self.locations.append(self.Loc(e, node, i))

    def visit_field_access(self, node):
        super().visit_field_access(node)
        if node.expr:
            self.locations.append(self.Loc(node.expr, node, 0))

    def visit_func_call(self, node):
        super().visit_func_call(node)
        j = 0
        if node.receiver:
            self.locations.append(self.Loc(node.receiver, node, 0))
            j = 1
        for i, p in enumerate(node.args):
            if isinstance(p, ast.CallArgument):
                self.locations.append(self.Loc(p.expr, node, i + j))
            else:
                self.locations.append(self.Loc(p, node, i + j))

    def visit_assign(self, node):
        super().visit_assign(node)
        j = 0
        if node.receiver:
            self.locations.append(self.Loc(node.receiver, node, j))
            j = 1
        self.locations.append(self.Loc(node.expr, node, j))

    def get_candidate_program_locations(self):
        self.visit_program(self.program)
        return self.locations

    def filter_program_locations(self, locations):
        filtered_locs = []
        for elem, parent, index in locations:
            if not elem.is_typed():
                continue
            exp_t, actual_t = elem.get_type_info()
            if exp_t[0] is None:
                continue
            t = exp_t[0]
            if t in [
                    self.bt_factory.get_any_type(),
                    self.bt_factory.get_boolean_type(),
                    self.bt_factory.get_boolean_type(primitive=True),
                    self.bt_factory.get_string_type()
            ]:
                continue
            filtered_locs.append(self.Loc(elem, parent, index))
        return filtered_locs

    def enumerate_programs(self, locations):
        pass
