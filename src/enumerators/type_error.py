from typing import NamedTuple

from src.enumerators.updater import ProgramUpdate
from src.enumerators.error import ErrorEnumerator
from src.ir import ast, type_utils as tu
from src.ir.builtins import BuiltinFactory
from src.generators import Generator


class Loc(NamedTuple):
    expr: ast.Expr
    parent: ast.Node
    index: int


class TypeErrorEnumerator(ErrorEnumerator):

    def __init__(self, program: ast.Program, program_gen: Generator,
                 bt_factory: BuiltinFactory):
        self.locations = []
        self.api_graph = program_gen.api_graph
        super().__init__(program, program_gen, bt_factory)

    def visit_block(self, node):
        super().visit_block(node)
        for i, elem in enumerate(node.body):
            if isinstance(elem, ast.Expr):
                self.locations.append(Loc(elem, node, i))

    def visit_var_decl(self, node):
        super().visit_var_decl(node)
        self.locations.append(Loc(node.expr, node, 0))

    def visit_func_decl(self, node):
        super().visit_func_decl(node)
        if node.body and isinstance(node.body, ast.Expr):
            self.locations.append(Loc(node.body, node, 0))

    def visit_lambda(self, node):
        super().visit_lambda(node)
        if isinstance(node.body, ast.Expr):
            self.locations.append(Loc(node.body, node, 0))

    def visit_func_ref(self, node):
        super().visit_func_ref(node)
        if node.receiver:
            self.locations.append(Loc(node.receiver, node, 0))

    def visit_array_expr(self, node):
        super().visit_array_expr(node)
        for i, expr in enumerate(node.exprs):
            self.locations.append(Loc(expr, node, i))

    def visit_logical_expr(self, node):
        super().visit_logical_expr(node)
        self.locations.append(Loc(node.lexpr, node, 0))
        self.locations.append(Loc(node.rexpr, node, 1))

    def visit_equality_expr(self, node):
        super().visit_equality_expr(node)
        self.locations.append(Loc(node.lexpr, node, 0))
        self.locations.append(Loc(node.rexpr, node, 1))

    def visit_comparison_expr(self, node):
        super().visit_comparison_expr(node)
        self.locations.append(Loc(node.lexpr, node, 0))
        self.locations.append(Loc(node.rexpr, node, 1))

    def visit_arith_expr(self, node):
        super().visit_arith_expr(node)
        self.locations.append(Loc(node.lexpr, node, 0))
        self.locations.append(Loc(node.rexpr, node, 1))

    def visit_conditional(self, node):
        super().visit_conditional(node)
        self.locations.append(Loc(node.cond, node, 0))
        if isinstance(node.true_branch, ast.Expr):
            self.locations.append(Loc(node.true_branch, node, 1))
        if isinstance(node.false_branch, ast.Expr):
            self.locations.append(Loc(node.false_branch, node, 1))

    def visit_new(self, node):
        super().visit_new(node)
        for i, e in enumerate(node.args):
            self.locations.append(Loc(e, node, i))

    def visit_field_access(self, node):
        super().visit_field_access(node)
        if node.expr:
            self.locations.append(Loc(node.expr, node, 0))

    def visit_func_call(self, node):
        super().visit_func_call(node)
        j = 0
        if node.receiver:
            self.locations.append(Loc(node.receiver, node, 0))
            j = 1
        for i, p in enumerate(node.args):
            if isinstance(p, ast.CallArgument):
                self.locations.append(Loc(p.expr, node, i + j))
            else:
                self.locations.append(Loc(p, node, i + j))

    def visit_assign(self, node):
        super().visit_assign(node)
        j = 0
        if node.receiver:
            self.locations.append(Loc(node.receiver, node, j))
            j = 1
        self.locations.append(Loc(node.expr, node, j))

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
            filtered_locs.append(Loc(elem, parent, index))
        return filtered_locs

    def get_programs_with_error(self, loc):
        exp_t, actual_t = loc.expr.get_type_info()
        exp_t, type_vars = exp_t
        if type_vars:
            # TODO
            return None
        types = self.api_graph.get_reg_types()
        try:
            for t in types:
                if t.is_subtype(exp_t):
                    continue
                if t.is_type_constructor():
                    t = tu.instantiate_type_constructor(
                        t, types, only_regular=True,
                        rec_bound_handler=self.api_graph.get_instantiations_of_recursive_bound)
                    if t is None:
                        continue
                self.program_gen.block_variables = True
                expr = self.program_gen._generate_expr_from_node(t, depth=1)
                self.program_gen.block_variables = False
                upd = ProgramUpdate(loc.index, expr.expr)
                upd.visit(loc.parent)
                self.add_err_message(loc, expr.expr, t)
                yield self.program
        except Exception as e:
            self.program_gen.block_variables = False
            raise e

    def add_err_message(self, loc, new_node, *args):
        exp_t = loc.expr.get_type_info()[0][0]
        actual_t = args[0]
        translator = self.program_gen.translator
        exp_t = translator.get_type_name(exp_t)
        actual_t = translator.get_type_name(actual_t)
        translator.context = self.program.context
        translator.visit(new_node)
        expr = translator._children_res[-1]
        translator._reset_state()
        msg = f"Expected type {exp_t}, but type {actual_t} given: {expr}"
        self.error_injected = msg
