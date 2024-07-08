import itertools
from typing import List, Generator, Set

from src import utils
from src.enumerators import type_abstractions as ta
from src.enumerators.analyses import Loc, ExprLocationAnalysis
from src.enumerators.error import ErrorEnumerator
from src.ir import ast, type_utils as tu, types as tp
from src.ir.builtins import BuiltinFactory
from src.ir.visitors import ASTExprUpdate
from src.generators import Generator as ProgramGenerator
from src.generators.api import nodes


def type_similarity(t: tp.Type, target: tp.Type,
                    bt_factory: BuiltinFactory) -> float:
    """
    Computes the distance of type t from the given target type. The distance
    is based on common ancestors. This means that the more common ancestors
    the type t has with target, the greater the similarity score is.

    The type similarity metric is based on the Jaccard index.
    """
    if target is None:
        return 0
    a = {a.name for a in t.get_supertypes() if a != bt_factory.get_any_type()}
    b = {a.name for a in target.get_supertypes()
         if a != bt_factory.get_any_type()}
    return len(a & b) / len(a | b)


class TypeErrorEnumerator(ErrorEnumerator):
    name = "TypeErrorEnumerator"

    NUMBER_OF_TYPE_PARAMETER_INSTANTIATIONS = 5

    NO_EXCLUSION = 0
    EXCLUDE_SUBTYPES = 1
    EXCLUDE_SUPERTYPES = 2

    def __init__(self, program: ast.Program, program_gen: ProgramGenerator,
                 bt_factory: BuiltinFactory):
        self.locations = []
        self.api_graph = program_gen.api_graph
        self.error_loc = None
        self.new_node = None
        self.depth = 0
        super().__init__(program, program_gen, bt_factory)

    @property
    def error_explanation(self):
        if self.error_loc is None:
            return
        loc = self.error_loc
        new_node = self.new_node
        exp_t, prev_actual_t = loc.expr.get_type_info()
        actual_t = new_node.get_type_info()[1]

        # Get the string representation of types
        translator = self.program_gen.translator
        exp_t = translator.get_type_name(exp_t or prev_actual_t)
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
               f" - New expression {expr}\n"
               f" - Receiver location: {self.error_loc.is_receiver_loc()}\n")
        return msg

    def add_err_message(self, loc, new_node):
        """
        Adds an error message explaining the type error that has been
        injected in the program.
        """
        self.error_loc = loc
        self.new_node = new_node

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
        if t is None:
            return set()
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
        byte_ = tuple(bytes_)[0]
        short_ = tuple(shorts)[0]
        int_ = tuple(ints)[0]
        long_ = tuple(longs)[0]
        float_ = tuple(floats)[0]
        double_ = tuple(doubles)[0]
        char_ = tuple(chars)[0]
        bool_ = tuple(booleans)[0]
        string = self.bt_factory.get_string_type()
        void = self.bt_factory.get_void_type()

        # TODO for the rest of the languages
        excluded_types = {
            "groovy": {
                byte_.name: bytes_,
                short_.name: bytes_ | shorts,
                int_.name: bytes_ | shorts | ints,
                long_.name: bytes_ | shorts | ints | longs,
                float_.name: bytes_ | shorts | ints | longs | floats,
                double_.name: bytes_ | shorts | ints | longs | floats | doubles,
                char_.name: chars,
                bool_.name: types,
                string.name: types,
                void.name: types,
            }
        }
        blacklisted_types = excluded_types.get(self.bt_factory.get_language())
        if blacklisted_types is None:
            return set()
        blacklisted_types = blacklisted_types.get(t.name, set())
        if isinstance(loc.parent, (ast.ComparisonExpr, ast.ArithExpr)):
            # If the parent is a comparison expression, extend the list of
            # the blacklisted types
            blacklisted_types = (
                blacklisted_types | bytes_ | shorts | ints | longs |
                floats | doubles | chars | booleans)
        return blacklisted_types

    def get_candidate_program_locations(self):
        analysis = ExprLocationAnalysis()
        analysis.visit_program(self.program)
        return analysis.locations

    def filter_program_locations(self, locations):
        filtered_locs = []
        cache = set()
        for elem, parent, index, depth in locations:
            if not elem.is_typed():
                continue
            exp_t, actual_t = elem.get_type_info()
            t = exp_t
            if t in [
                    self.bt_factory.get_any_type(),
                    self.bt_factory.get_boolean_type(),
                    self.bt_factory.get_boolean_type(primitive=True),
                    self.bt_factory.get_void_type(primitive=True),
                    self.bt_factory.get_void_type(primitive=False)
            ]:
                continue
            if (t is not None
                    and t.name == self.bt_factory.get_string_type().name):
                continue
            cached_elem = (type(parent), exp_t, depth, index)
            if cached_elem not in cache:
                filtered_locs.append(Loc(elem, parent, index, depth))
                cache.add(cached_elem)
        return filtered_locs

    def _get_exclusion_strategies(self,
                                  variances: Set[tp.Variance]) -> Set[int]:
        exclusion_strategies = set()
        for variance in variances:
            if variance.is_invariant():
                continue
            if variance.is_covariant():
                exclusion_strategies.add(self.EXCLUDE_SUBTYPES)

            if variance.is_contravariant():
                exclusion_strategies.add(self.EXCLUDE_SUPERTYPES)
        return exclusion_strategies

    def enumerate_incompatible_typings(self, loc):
        if loc.is_receiver_loc():
            # For receiver expression, we have a different logic to enumerate
            # the incompatible typings.
            yield from self.get_incompatible_type_of_receiver(loc)
            return
        exp_t, actual_t = loc.expr.get_type_info()
        candidate_types = self.get_representative_types(loc, exp_t)
        candidate_types = sorted(
            list(candidate_types),
            key=lambda x: type_similarity(x, exp_t, self.bt_factory),
            reverse=True
        )
        for i, t in enumerate(candidate_types):
            yield from self.get_incompatible_type(t, exp_t, loc)

    def get_programs_with_error(self, loc):
        exp_t, actual_t = loc.expr.get_type_info()
        try:
            for incompatible_t in self.enumerate_incompatible_typings(loc):
                self.program_gen.block_variables = True
                expr = self.program_gen._generate_expr_from_node(
                    incompatible_t, depth=1)
                expr.expr.mk_typed(ast.TypePair(expected=exp_t,
                                                actual=incompatible_t))
                self.program_gen.block_variables = False
                upd = ASTExprUpdate(loc.index, expr.expr)
                upd.visit(loc.parent)
                self.add_err_message(loc, expr.expr)
                yield self.program
        except Exception as e:
            self.program_gen.block_variables = False
            raise e
        return None

    def get_representative_types(self, loc: Loc, exp_t: tp.Type,
                                 exclude_strategy: Set[int] = None):
        exclude_strategy = exclude_strategy or []
        type_pool = self.api_graph.get_reg_types()
        excluded_types = self.get_type_filters(loc, exp_t, type_pool)
        types = set()
        for t in type_pool:
            if t in excluded_types:
                continue
            if not exclude_strategy:
                types.add(t)
            if self.EXCLUDE_SUBTYPES in exclude_strategy:
                if not t.is_subtype(exp_t):
                    types.add(t)
            if self.EXCLUDE_SUPERTYPES in exclude_strategy:
                if t not in exp_t.get_supertypes():
                    types.add(t)

        # Abstract every type included in the set.
        types_map = {t: ta.to_type_abstraction(t, self.bt_factory)
                     for t in types}
        abstractions = set(types_map.values())
        # Group types based on their abstraction
        type_classes = [[k for k, v in types_map.items()
                        if v == x] for x in abstractions]
        candidate_types = []
        for type_class in type_classes:
            type_class = [t for t in type_class
                          if t not in excluded_types]
            if type_class:
                candidate_types.append(utils.random.choice(type_class))
        return candidate_types

    def get_declarations_of_receiver(self,
                                     expr: ast.Expr) -> List[ast.Declaration]:
        """
        Given an expression, such as function call, field access, or
        assignment, this methos retrieves all the instance declarations
        associated with this expression.
        """
        assert isinstance(expr, (ast.FunctionCall, ast.FieldAccess,
                                 ast.Assignment, ast.FunctionReference))
        if expr.receiver is None:
            return []
        receiver_type = expr.receiver.get_type_info()[1]
        if isinstance(expr, (ast.FunctionCall, ast.FunctionReference)):
            # Handle function calls
            func_name, cls = expr.func, None
            segs = func_name.rsplit(".", 1)
            if len(segs) == 2:
                cls, func_name = tuple(segs)
            # Create a dummy method with the same name.
            # Find its overloaded methods.
            dummy_method = nodes.Method(func_name, cls, [], [], {})
            return [m for m, _ in self.api_graph.get_overloaded_methods(
                receiver_type, dummy_method, override_checks_with_self=False)]
        field_name = (expr.field
                      if isinstance(expr, ast.FieldAccess)
                      else expr.name)
        field = self.api_graph.get_field(receiver_type, field_name)
        return [] if field is None else [field]

    def get_type_variables_of_node_signature(self, node: nodes.APINode,
                                             receiver_type: tp.Type):
        """
        Given a particular node in the API graph, this method takes all the
        type variables that are found in its signature.
        """
        type_variables = set()
        parent = self.api_graph.get_type_by_name(node.cls)
        assert parent is not None
        sub = tu.get_type_substitution_of_parent(parent, receiver_type)
        if isinstance(node, (nodes.Method, nodes.Constructor)):
            for p in node.parameters:
                type_variables.update(tu.get_type_variables_of_type(
                    tp.substitute_type(p.t, sub)))
        out_type = self.api_graph.get_concrete_output_type(node)
        type_variables.update(tu.get_type_variables_of_type(
            tp.substitute_type(out_type, sub)))
        return type_variables

    def has_applicable_method(self, candidate_t: tp.Type,
                              func_call: ast.FunctionCall,
                              index: int) -> bool:
        """
        This method checks whether the given candidate type can be given
        as an argument of another overloaded method. If this is the case,
        it will result in a well-typed program, as another (valid) method
        will be resolved by the compiler.
        """
        # We do this check only when the parent expression is a function call.
        if not isinstance(func_call, ast.FunctionCall):
            return False

        # ... that takes at least one parameter, and the current index does
        # not correspond to the receiver of the method.
        if not func_call.args or (func_call.receiver and index == 0):
            return False
        overloaded_methods = self.get_declarations_of_receiver(func_call)
        if len(overloaded_methods) < 2:
            return False
        param_index = index if func_call.receiver is None else index - 1
        # There is an applicable method for the given candidate type when
        # the following conditions are met:
        #   * The given candidate type matches with the expected formal
        #     parameter type of the method in the same index.
        #   * The overloaded method has the same number of parameters as the
        #     number of the given arguments in the funtion.
        return any(m for m in overloaded_methods
                   if len(m.parameters) > param_index
                   and candidate_t.is_subtype(m.parameters[param_index].t)
                   and len(m.paremeters) == len(func_call.args))

    def replace_receiver_type(self, loc: Loc, receiver_type: tp.Type,
                              type_variables: List[tp.TypeParameter]):
        """
        Given a receiver type, this method replaces the type parameters
        of the receiver type with various incompatible type arguments.
        """
        sub = receiver_type.get_type_variable_assignments()
        rec_t, type_con = receiver_type, receiver_type.t_constructor
        # Get the functional type of the receiver type
        receiver_type = (self.api_graph.get_functional_type(receiver_type)
                         or receiver_type)
        mapped_type_vars = {}
        for k, v in receiver_type.get_type_variable_assignments().items():
            if v in type_variables:
                mapped_type_vars.setdefault(v, set()).add(k.variance)
        for type_var in type_variables:
            type_arg = sub[type_var]
            for new_type_arg in self.get_type_parameter_instantiations(
                    type_arg, loc, type_con, mapped_type_vars[type_var]):
                if new_type_arg.is_type_constructor():
                    new_type_arg = tu.instantiate_type_constructor(
                        new_type_arg, self.api_graph.get_reg_types(),
                        only_regular=True,
                        rec_bound_handler=self.api_graph.get_instantiations_of_recursive_bound)
                    if new_type_arg is None:
                        return
                    new_type_arg = new_type_arg[0]
                type_args = list(rec_t.type_args)
                index = type_con.type_parameters.index(type_var)
                type_args[index] = new_type_arg
                yield type_con.new(type_args)

    def find_applicable_method(
            self, loc: Loc,
            overloaded_methods: Set[nodes.Method]) -> nodes.Method:
        """
        Based on a set of overloaded methods, select one that is applicable.
        Note this is implementation is a heuristic and incomplete. The decision
        is made based on the number of arguments/formal parameters.
        """
        if isinstance(loc.expr, ast.FunctionCall):
            args = loc.expr.args
            overloaded_methods = [m for m in overloaded_methods
                                  if len(m.parameters) == len(args)]
        else:
            assert isinstance(loc.expr, ast.FunctionReference)
            functional_type = self.api_graph.get_functional_type(
                loc.expr.get_type_info()[1])
            overloaded_methods = [
                m for m in overloaded_methods
                if len(m.parameters) == len(functional_type.type_args) - 1
            ]
        if not overloaded_methods:
            return None
        return overloaded_methods[0]

    def get_incompatible_type_of_receiver(self, loc: Loc):
        """
        Based on a given location that corresponds to a receiver expression,
        this method produces a type that is an forms an incompatible receiver.
        """
        assert loc.parent.receiver is not None, (
            "Assertion failed: parent location does not contain a receiver")
        declarations = self.get_declarations_of_receiver(loc.parent)
        if len(declarations) > 1:
            decl = self.find_applicable_method(loc, declarations)
        receiver_type = loc.parent.receiver.get_type_info()[1]
        if not receiver_type.is_parameterized():
            return None
        decl = declarations[0]
        type_variables = self.get_type_variables_of_node_signature(
            decl, receiver_type)
        type_variables = set(
            receiver_type.t_constructor.type_parameters) & type_variables
        if not type_variables:
            return None
        yield from self.replace_receiver_type(loc, receiver_type,
                                              type_variables)

    def get_incompatible_type(self, candidate_t: tp.Type,
                              exp_t: tp.Type, loc: Loc) -> bool:
        """
        Given a candidate type t1 and an expected type t2, this method checks
        whether t1 is incompatible to t2.
        """
        if not candidate_t.is_type_constructor():
            if (not candidate_t.is_subtype(exp_t) and
                    not self.has_applicable_method(candidate_t, loc.parent,
                                                   loc.index)):
                yield candidate_t
            return

        t = candidate_t.new([tp.WildCardType()
                             for _ in candidate_t.type_parameters])
        if t.is_subtype(exp_t) and t.name != exp_t.name:
            return None

        yield from self.gen_incompatible_type_constructor_instantiations(
            exp_t, loc, candidate_t
        )

    def get_type_parameter_instantiations(self, type_arg: tp.Type,
                                          loc: Loc,
                                          type_con: tp.TypeConstructor,
                                          variances: Set[tp.Variance]):
        instantiations = []
        if type_con != self.bt_factory.get_array_type():
            # Wildcards
            instantiations.append(tp.WildCardType())
            if tp.Covariant in variances:
                instantiations.append(tp.WildCardType(type_arg,
                                                      tp.Contravariant))
            if tp.Contravariant in variances:
                instantiations.append(tp.WildCardType(type_arg, tp.Covariant))

        exp_t = loc.expr.get_type_info()[1]
        # Nested
        instantiations.append(exp_t)
        if type_arg.is_parameterized():
            instantiations.append(type_arg.type_args[0])
        candidate_types = self.get_representative_types(
            loc, type_arg, self._get_exclusion_strategies(variances))
        candidate_types = sorted(
            list(candidate_types),
            key=lambda x: type_similarity(x, exp_t, self.bt_factory),
            reverse=True
        )
        instantiations.extend(candidate_types)
        return instantiations

    def _gen_incompatible_parameterized_type_from_non_polymorphic_type(
        self,
        exp_t: tp.Type,
        type_con: tp.TypeConstructor
    ) -> tp.ParameterizedType:
        types = self.api_graph.get_reg_types()
        supertypes = [t for t in exp_t.get_supertypes()
                      if t.name == type_con.name]
        if not supertypes:
            param_t = tu.instantiate_type_constructor(
                type_con, types,
                only_regular=True,
                rec_bound_handler=self.api_graph.get_instantiations_of_recursive_bound
            )
            return param_t[0] if param_t is not None else param_t
        return supertypes[0]

    def _gen_incompatible_instantiations_from_related_type(
        self, exp_t: tp.Type,
        type_con: tp.TypeConstructor,
        sub: dict, loc: Loc
    ) -> list:
        types = self.api_graph.get_reg_types()
        reversed_sub = {}
        for k, v in sub.items():
            reversed_sub.setdefault(v, []).append(k)

        instantiations = {}
        type_var_assignments = exp_t.get_type_variable_assignments()
        indexes = {}
        for i, (type_param, v) in enumerate(reversed_sub.items()):
            indexes[i] = type_param
            t = utils.random.choice(v)
            if not t.is_type_var():
                instantiations.setdefault(type_param, []).append(t)
            else:
                type_arg = type_var_assignments[t]
                variances = set()
                if type_arg.is_wildcard() and type_arg.variance.is_covariant():
                    variances.add(tp.Covariant)
                    type_arg = type_arg.bound

                if type_arg.is_wildcard() and type_arg.is_contravariant():
                    variances.add(tp.Contravariant)
                    type_arg = type_arg.bound
                instantiations.setdefault(type_param, []).extend(
                    self.get_type_parameter_instantiations(
                        type_arg, loc, type_con, variances))

        subs = []
        for comb in itertools.product(*instantiations.values()):
            subs.append({
                indexes[i]: elem
                for i, elem in enumerate(comb)
            })
        return subs

    def gen_incompatible_type_constructor_instantiations(
        self,
        exp_t: tp.Type,
        loc: Loc,
        type_con: tp.TypeConstructor,
    ) -> Generator[tp.ParameterizedType, None, None]:
        """
        Given an expected type and a type constructor T, this function yields
        various instantiations of T so that the resulting parameterized type
        is not compatible with the given expected type.
        """
        types = self.api_graph.get_reg_types()
        if not exp_t.is_parameterized():
            yield self._gen_incompatible_parameterized_type_from_non_polymorphic_type(
                exp_t, type_con)
            return
        sub = tu.get_type_substitution_of_parent(exp_t, type_con)
        param_t = tu.instantiate_type_constructor(
            type_con, types, only_regular=True,
            rec_bound_handler=self.api_graph.get_instantiations_of_recursive_bound
        )
        if param_t is None:
            return
        if not sub:
            # They types don't have connected type parameters. So, just return
            # an instantation of the candidate type constructor.
            yield param_t[0]
            return
        param_t = param_t[0]
        instantiations = \
            self._gen_incompatible_instantiations_from_related_type(
                exp_t, type_con, sub, loc)

        for type_var_map in instantiations:
            # Here, we replace the instantiations of the connected type
            # parameters with incompatible types.
            type_args = list(param_t.type_args)
            for k, v in type_var_map.items():
                index = type_con.type_parameters.index(k)
                type_args[index] = v
            yield type_con.new(type_args)
