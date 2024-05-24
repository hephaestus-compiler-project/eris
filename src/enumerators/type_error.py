import itertools
from typing import NamedTuple, List, Generator

from src.enumerators.error import ErrorEnumerator
from src.ir import ast, type_utils as tu, types as tp
from src.ir.builtins import BuiltinFactory
from src.ir.visitors import ASTExprUpdate
from src.generators import Generator as ProgramGenerator


class Loc(NamedTuple):
    expr: ast.Expr
    parent: ast.Node
    index: int
    depth: int



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
    name = "TypeErrorEnumerator"

    def __init__(self, program: ast.Program, program_gen: ProgramGenerator,
                 bt_factory: BuiltinFactory):
        self.locations = []
        self.api_graph = program_gen.api_graph
        self.error_loc = None
        self.new_node = None
        self.depth = 0
        super().__init__(program, program_gen, bt_factory)

    def visit_block(self, node):
        super().visit_block(node)
        for i, elem in enumerate(node.body):
            if isinstance(elem, ast.Expr):
                self.locations.append(Loc(elem, node, i, self.depth))

    def visit_var_decl(self, node):
        prev_depth = self.depth
        self.depth += 1
        super().visit_var_decl(node)
        self.depth = prev_depth
        self.locations.append(Loc(node.expr, node, 0, self.depth))

    def visit_func_decl(self, node):
        prev_depth = self.depth
        self.depth += 1
        super().visit_func_decl(node)
        self.depth = prev_depth
        if node.body and isinstance(node.body, ast.Expr):
            self.locations.append(Loc(node.body, node, 0, self.depth))

    def visit_lambda(self, node):
        prev_depth = self.depth
        self.depth += 1
        super().visit_lambda(node)
        self.depth = prev_depth
        if isinstance(node.body, ast.Expr):
            self.locations.append(Loc(node.body, node, 0, self.depth))

    def visit_func_ref(self, node):
        prev_depth = self.depth
        self.depth += 1
        super().visit_func_ref(node)
        self.depth = prev_depth
        if node.receiver:
            self.locations.append(Loc(node.receiver, node, 0, self.depth))

    def visit_array_expr(self, node):
        prev_depth = self.depth
        self.depth += 1
        super().visit_array_expr(node)
        self.depth = prev_depth
        for i, expr in enumerate(node.exprs):
            self.locations.append(Loc(expr, node, i, self.depth))

    def visit_binary_expr(self, node):
        prev_depth = self.depth
        self.depth += 1
        super().visit_binary_expr(node)
        self.depth = prev_depth
        self.locations.append(Loc(node.lexpr, node, 0, self.depth))
        self.locations.append(Loc(node.rexpr, node, 1, self.depth))

    def visit_logical_expr(self, node):
        prev_depth = self.depth
        self.depth += 1
        super().visit_logical_expr(node)
        self.depth = prev_depth
        self.locations.append(Loc(node.lexpr, node, 0, self.depth))
        self.locations.append(Loc(node.rexpr, node, 1, self.depth))

    def visit_equality_expr(self, node):
        prev_depth = self.depth
        self.depth += 1
        super().visit_equality_expr(node)
        self.depth = prev_depth
        self.locations.append(Loc(node.lexpr, node, 0, self.depth))
        self.locations.append(Loc(node.rexpr, node, 1, self.depth))

    def visit_comparison_expr(self, node):
        prev_depth = self.depth
        self.depth += 1
        super().visit_comparison_expr(node)
        self.depth = prev_depth
        self.locations.append(Loc(node.lexpr, node, 0, self.depth))
        self.locations.append(Loc(node.rexpr, node, 1, self.depth))

    def visit_arith_expr(self, node):
        prev_depth = self.depth
        self.depth += 1
        super().visit_arith_expr(node)
        self.depth = prev_depth
        self.locations.append(Loc(node.lexpr, node, 0, self.depth))
        self.locations.append(Loc(node.rexpr, node, 1, self.depth))

    def visit_conditional(self, node):
        prev_depth = self.depth
        self.depth += 1
        super().visit_conditional(node)
        self.depth = prev_depth
        self.locations.append(Loc(node.cond, node, 0, self.depth))
        if isinstance(node.true_branch, ast.Expr):
            self.locations.append(Loc(node.true_branch, node, 1, self.depth))
        if isinstance(node.false_branch, ast.Expr):
            self.locations.append(Loc(node.false_branch, node, 2, self.depth))

    def visit_new(self, node):
        prev_depth = self.depth
        self.depth += 1
        super().visit_new(node)
        self.depth = prev_depth
        for i, e in enumerate(node.args):
            self.locations.append(Loc(e, node, i, self.depth))

    def visit_field_access(self, node):
        prev_depth = self.depth
        self.depth += 1
        super().visit_field_access(node)
        self.depth = prev_depth
        if node.expr:
            self.locations.append(Loc(node.expr, node, 0, self.depth))

    def visit_func_call(self, node):
        prev_depth = self.depth
        self.depth += 1
        super().visit_func_call(node)
        self.depth = prev_depth
        j = 0
        if node.receiver:
            self.locations.append(Loc(node.receiver, node, 0, self.depth))
            j = 1
        for i, p in enumerate(node.args):
            if isinstance(p, ast.CallArgument):
                self.locations.append(Loc(p.expr, node, i + j, self.depth))
            else:
                self.locations.append(Loc(p, node, i + j, self.depth))

    def visit_assign(self, node):
        prev_depth = self.depth
        self.depth += 1
        super().visit_assign(node)
        self.depth = prev_depth
        j = 0
        if node.receiver:
            self.locations.append(Loc(node.receiver, node, j, self.depth))
            j = 1
        self.locations.append(Loc(node.expr, node, j, self.depth))

    def get_builtin_types(self):
        byte = self.bt_factory.get_byte_type()
        short = self.bt_factory.get_short_type()
        int_ = self.bt_factory.get_integer_type()
        long = self.bt_factory.get_long_type()
        float_ = self.bt_factory.get_float_type()
        double = self.bt_factory.get_double_type()
        char = self.bt_factory.get_char_type()
        boolean = self.bt_factory.get_boolean_type()
        string = self.bt_factory.get_string_type()

        return {
            "byte": self.api_graph.get_type_by_name(byte.name) or byte,
            "short": self.api_graph.get_type_by_name(short.name) or short,
            "int": self.api_graph.get_type_by_name(int_.name) or int_,
            "long": self.api_graph.get_type_by_name(long.name) or long,
            "float": self.api_graph.get_type_by_name(float_.name) or float_,
            "double": self.api_graph.get_type_by_name(double.name) or double,
            "char": self.api_graph.get_type_by_name(char.name) or char,
            "boolean": self.api_graph.get_type_by_name(boolean.name) or boolean,
            "string": self.api_graph.get_type_by_name(string.name) or string,
        }

    def get_type_filters(self, loc: Loc, t: tp.Type,
                         types: List[tp.Type]) -> List[tp.Type]:
        builtins = self.get_builtin_types()

        bytes_ = {self.bt_factory.get_byte_type(primitive=True),
                  builtins["byte"]}
        shorts = {self.bt_factory.get_short_type(primitive=True),
                  builtins["short"]}
        ints = {self.bt_factory.get_integer_type(primitive=True),
                builtins["int"]}
        longs = {self.bt_factory.get_long_type(primitive=True),
                 builtins["long"]}
        floats = {self.bt_factory.get_float_type(primitive=True),
                  builtins["float"]}
        doubles = {self.bt_factory.get_double_type(primitive=True),
                   builtins["double"]}
        booleans = {self.bt_factory.get_boolean_type(primitive=True),
                    builtins["boolean"]}
        chars = {self.bt_factory.get_char_type(primitive=True),
                 builtins["char"]}
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
            }
        }
        blacklisted_types = excluded_types.get(self.bt_factory.get_language())
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

    def get_candidate_program_locations(self):
        self.visit_program(self.program)
        return self.locations

    def filter_program_locations(self, locations):
        filtered_locs = []
        cache = set()
        for elem, parent, index, depth in locations:
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
                    self.bt_factory.get_void_type()
            ]:
                continue
            if t.name == "String":
                continue
            cached_elem = (type(parent), exp_t, depth, index)
            if cached_elem not in cache:
                filtered_locs.append(Loc(elem, parent, index, depth))
                cache.add(cached_elem)

        return filtered_locs

    def get_programs_with_error(self, loc):
        exp_t, actual_t = loc.expr.get_type_info()
        types = self.api_graph.get_reg_types()
        try:
            excluded_types = self.get_type_filters(loc, exp_t, types)
            excluded_types = {t.name for t in excluded_types}
            candidate_types = {t for t in types
                               if t.name not in excluded_types}
            candidate_types = sorted(
                list(candidate_types),
                key=lambda x: type_similarity(x, exp_t, self.bt_factory),
                reverse=True
            )
            for t in candidate_types:
                incompatible_types = self.get_incompatible_type(t, exp_t)
                if incompatible_types is None:
                    continue
                for incompatible_t in incompatible_types:
                    self.program_gen.block_variables = True
                    expr = self.program_gen._generate_expr_from_node(
                        incompatible_t, depth=1)
                    self.program_gen.block_variables = False
                    upd = ASTExprUpdate(loc.index, expr.expr)
                    upd.visit(loc.parent)
                    self.add_err_message(loc, expr.expr)
                    yield self.program
        except Exception as e:
            self.program_gen.block_variables = False
            raise e
        return None

    @property
    def error_explanation(self):
        if self.error_loc is None:
            return
        loc = self.error_loc
        new_node = self.new_node
        exp_t, _ = loc.expr.get_type_info()
        actual_t = new_node.get_type_info()[1]

        # Get the string representation of types
        translator = self.program_gen.translator
        exp_t = translator.get_type_name(exp_t)
        actual_t = translator.get_type_name(actual_t)

        # Get the string representation of expressions
        translator.context = self.program.context
        translator.visit(new_node)
        expr = translator._children_res[-1]
        translator._reset_state()
        translator.context = self.program.context
        translator.visit(loc.expr)
        previous_expr = translator._children_res[-1]
        translator._reset_state()

        msg = (f"Added type error using {self.name}:\n"
               f" - Expected type: {exp_t}\n"
               f" - Actual type: {actual_t}\n"
               f" - Previous expression {previous_expr}\n"
               f" - New expression {expr}\n")
        return msg

    def add_err_message(self, loc, new_node):
        """
        Adds an error message explaining the type error that has been
        injected in the program.
        """
        self.error_loc = loc
        self.new_node = new_node

    def get_incompatible_type(self, candidate_t: tp.Type,
                              exp_t: tp.Type) -> bool:
        """
        Given a candidate type t1 and an expected type t2, this method checks
        whether t1 is incompatible to t2.
        """
        if not candidate_t.is_type_constructor():
            if not candidate_t.is_subtype(exp_t):
                yield candidate_t
            return

        t = candidate_t.new([tp.WildCardType()
                             for _ in candidate_t.type_parameters])
        if t.is_subtype(exp_t) and t.name != exp_t.name:
            return None

        yield from self.gen_incompatible_type_constructor_instantiations(
            exp_t, candidate_t
        )

    def get_type_parameter_instantiations(self, type_arg: tp.Type,
                                          exp_t: tp.Type,
                                          type_con: tp.TypeConstructor,
                                          types: List[tp.Type]):
        instantiations = []
        if type_con != self.bt_factory.get_array_type():
            # Wildcards
            instantiations.extend([
                tp.WildCardType(),
                tp.WildCardType(bound=type_arg, variance=tp.Covariant),
                tp.WildCardType(bound=type_arg, variance=tp.Contravariant),
            ])

        # Nested
        instantiations.append(exp_t)
        if type_arg.is_parameterized():
            instantiations.append(type_arg.type_args[0])

        candidate_types = sorted(
            [t for t in types if not t.is_subtype(type_arg)],  # FIXME consider variance and upper bounds,
            key=lambda x: type_similarity(x, type_arg, self.bt_factory),
            reverse=True
        )
        len_ = min(5, len(candidate_types))  # TODO add option/magic number
        for t in candidate_types[:len_]:
            instantiations.append(t)
        return instantiations

    def gen_incompatible_type_constructor_instantiations(
        self,
        exp_t: tp.Type,
        type_con: tp.TypeConstructor,
    ) -> Generator[tp.ParameterizedType, None, None]:
        """
        Given an expected type and a type constructor T, this function yields
        various instantiations of T so that the resulting parameterized type
        is not compatible with the given expected type.
        """
        types = self.api_graph.get_reg_types()
        if not exp_t.is_parameterized():
            supertypes = [t for t in exp_t.get_supertypes()
                          if t.name == type_con.name]
            if not supertypes:
                candidate_t, _ = tu.instantiate_type_constructor(
                    type_con, types,
                    only_regular=True,
                    rec_bound_handler=self.api_graph.get_instantiations_of_recursive_bound)
                yield candidate_t
                return
            yield supertypes[0]
            return

        if exp_t.has_wildcards():
            # TODO
            return
        instantiations = []
        for i in range(len(type_con.type_parameters)):
            index = min(i, len(exp_t.type_args) - 1)
            type_arg = exp_t.type_args[index]
            instantiations.append(self.get_type_parameter_instantiations(
                type_arg, exp_t, type_con, types))
        for comb in itertools.product(*instantiations):
            yield type_con.new(comb)
