from typing import NamedTuple, List

from src.enumerators.error import ErrorEnumerator
from src.ir import ast, type_utils as tu, types as tp
from src.ir.builtins import BuiltinFactory
from src.ir.visitors import ASTExprUpdate
from src.generators import Generator


class Loc(NamedTuple):
    expr: ast.Expr
    parent: ast.Node
    index: int


def get_type_filters(bt_factory: BuiltinFactory, loc: Loc, t: tp.Type,
                     types: List[tp.Type]) -> List[tp.Type]:

    bytes_ = {bt_factory.get_byte_type(primitive=True),
              bt_factory.get_byte_type(primitive=False)}
    shorts = {bt_factory.get_short_type(primitive=True),
              bt_factory.get_short_type(primitive=False)}
    ints = {bt_factory.get_integer_type(primitive=True),
            bt_factory.get_integer_type(primitive=False)}
    longs = {bt_factory.get_long_type(primitive=True),
             bt_factory.get_long_type(primitive=False)}
    floats = {bt_factory.get_float_type(primitive=True),
              bt_factory.get_float_type(primitive=False)}
    doubles = {bt_factory.get_double_type(primitive=True),
               bt_factory.get_double_type(primitive=False)}
    booleans = {bt_factory.get_boolean_type(primitive=True),
                bt_factory.get_boolean_type(primitive=False)}
    chars = {bt_factory.get_char_type(primitive=True),
             bt_factory.get_char_type(primitive=False)}
    byte_p, byte_w = tuple(bytes_)
    short_p, short_w = tuple(shorts)
    int_p, int_w = tuple(ints)
    long_p, long_w = tuple(longs)
    float_p, float_w = tuple(floats)
    double_p, double_w = tuple(doubles)
    char_p, char_w = tuple(chars)
    bool_p, bool_w = tuple(booleans)

    # TODO for the rest of the languages
    excluded_types = {
        "groovy": {
            byte_p: bytes_,
            byte_w: bytes_,
            short_p: bytes_ | shorts,
            short_w: bytes_ | shorts,
            int_p: bytes_ | shorts | ints,
            int_w: bytes_ | shorts | ints,
            long_p: bytes_ | shorts | ints | longs,
            long_w: bytes_ | shorts | ints | longs,
            float_p: bytes_ | shorts | ints | longs | floats,
            float_w: bytes_ | shorts | ints | longs | floats,
            double_p: bytes_ | shorts | ints | longs | floats | doubles,
            double_w: bytes_ | shorts | ints | longs | floats | doubles,
            char_p: chars,
            char_w: chars,
            bool_p: types,
            bool_w: types,
            bt_factory.get_string_type(): types
        }
    }
    blacklisted_types = excluded_types.get(bt_factory.get_language())
    if blacklisted_types is None:
        return set()
    blacklisted_types = blacklisted_types.get(t, set())
    if isinstance(loc.parent, ast.ComparisonExpr):
        # If the parent is a comparison expression, extend the list of
        # the blacklisted types
        blacklisted_types = (
            blacklisted_types | bytes_ | shorts | ints | longs |
            floats | doubles | chars | booleans)
    return blacklisted_types


def type_similarity(t: tp.Type, target: tp.Type,
                    bt_factory: BuiltinFactory) -> float:
    """
    Computes the distance of type t from the given target type. The distance
    is based on common ancestors. This means that the more common ancestors
    the type t has with target, the greater the similarity score is.

    The type similarity metric is based on the Jaccard index.
    """
    a = {a.name for a in t.get_supertypes() if a != bt_factory.get_any_type()}
    b = {a.name for a in target.get_supertypes()
         if a != bt_factory.get_any_type()}
    return len(a & b) / len(a | b)


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

    def visit_binary_expr(self, node):
        super().visit_binary_expr(node)
        self.locations.append(Loc(node.lexpr, node, 0))
        self.locations.append(Loc(node.lexpr, node, 1))

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
            self.locations.append(Loc(node.false_branch, node, 2))

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
            if exp_t is None:
                continue
            t = exp_t
            if t in [
                    self.bt_factory.get_any_type(),
                    self.bt_factory.get_boolean_type(),
                    self.bt_factory.get_boolean_type(primitive=True),
            ]:
                continue
            if t.name == "String":
                continue
            filtered_locs.append(Loc(elem, parent, index))
        return filtered_locs

    def get_programs_with_error(self, loc):
        exp_t, actual_t = loc.expr.get_type_info()
        types = self.api_graph.get_reg_types()
        try:
            excluded_types = get_type_filters(self.bt_factory, loc, exp_t,
                                              types)
            candidate_types = {t for t in types if t not in excluded_types}
            candidate_types = sorted(
                list(candidate_types),
                key=lambda x: type_similarity(x, exp_t, self.bt_factory),
                reverse=True
            )
            for t in candidate_types:
                incompatible_t = self.get_incompatible_type(t, exp_t)
                self.program_gen.block_variables = True
                expr = self.program_gen._generate_expr_from_node(
                    incompatible_t, depth=1)
                self.program_gen.block_variables = False
                upd = ASTExprUpdate(loc.index, expr.expr)
                upd.visit(loc.parent)
                self.add_err_message(loc, expr.expr, incompatible_t)
                yield self.program
        except Exception as e:
            self.program_gen.block_variables = False
            raise e
        return None

    def add_err_message(self, loc, new_node, *args):
        """
        Adds an error message explaining the type error that has been
        injected in the program.
        """
        exp_t, _ = loc.expr.get_type_info()
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

    def get_incompatible_type(self, candidate_t: tp.Type,
                              exp_t: tp.Type) -> bool:
        """
        Given a candidate type t1 and an expected type t2, this method checks
        whether t1 is incompatible to t2.
        """
        if candidate_t.is_subtype(exp_t):
            return None
        if candidate_t.is_type_constructor():
            types = self.api_graph.get_reg_types()
            candidate_t, _ = tu.instantiate_type_constructor(
                candidate_t, types, only_regular=True,
                rec_bound_handler=self.api_graph.get_instantiations_of_recursive_bound)
            if candidate_t is None:
                return None
        return candidate_t
