from typing import Set, List, Generator

from src import utils
from src.enumerators import type_abstractions as ta
from src.ir import types as tp, type_utils as tu, ast
from src.ir.builtins import BuiltinFactory


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


def _add_base_type_arg(is_base_nullable: bool,
                       variances: Set[tp.Variance]) -> bool:
    return not (
        (tp.Covariant in variances and is_base_nullable) or
        tp.Contravariant in variances and not is_base_nullable
    )


class IncompatibleTyping():
    NO_EXCLUSION = 0
    EXCLUDE_SUBTYPES = 1
    EXCLUDE_SUPERTYPES = 2
    ONLY_SUBTYPES = 3
    ONLY_SUPERTYPES = 4

    def __init__(self, api_graph, bt_factory: BuiltinFactory):
        self.api_graph = api_graph
        self.bt_factory = bt_factory

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

    def get_type_filters(self, loc, t: tp.Type,
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
            },
            "java": {
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
            },
            "scala": {
                byte_.name: bytes_,
                short_.name: bytes_ | shorts,
                int_.name: bytes_ | shorts | ints,
                long_.name: bytes_ | shorts | ints | longs,
                float_.name: bytes_ | shorts | ints | longs | floats,
                double_.name: bytes_ | shorts | ints | longs | floats | doubles,
                char_.name: chars | ints | longs | floats | doubles,
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

    def get_representative_types(self, loc, exp_t: tp.Type,
                                 exclude_strategy: Set[int] = None,
                                 blacklisted_types=None):
        exclude_strategy = exclude_strategy or []
        blacklisted_types = blacklisted_types or set()
        type_pool = {t for t in self.api_graph.get_reg_types()
                     if t not in blacklisted_types}
        excluded_types = self.get_type_filters(loc, exp_t, type_pool)
        types = set()
        for t in type_pool:
            if t in excluded_types:
                continue
            if not exclude_strategy:
                types.add(t)
            if self.ONLY_SUBTYPES in exclude_strategy and t.is_subtype(exp_t):
                types.add(t)
                continue
            if (self.ONLY_SUPERTYPES in exclude_strategy and
                    t in exp_t.get_supertypes()):
                types.add(t)
                continue
            if self.EXCLUDE_SUBTYPES in exclude_strategy:
                if not t.is_subtype(exp_t):
                    types.add(t)
            if self.EXCLUDE_SUPERTYPES in exclude_strategy:
                if t not in exp_t.get_supertypes():
                    types.add(t)

        # Abstract every type included in the set.
        types_map = {t: ta.to_type_abstraction(t, self.bt_factory)
                     for t in types if t.name != exp_t.name}
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
        if exp_t.is_parameterized():
            candidate_types.append(exp_t.t_constructor)
        return candidate_types

    def enumerate_incompatible_typings(self, exp_t: tp.Type,
                                       loc):
        candidate_types = self.get_representative_types(loc, exp_t)
        candidate_types = sorted(
            list(candidate_types),
            key=lambda x: type_similarity(x, exp_t, self.bt_factory),
            reverse=True
        )
        for i, t in enumerate(candidate_types):
            yield from self.get_incompatible_type(t, exp_t, loc)

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
        if not func_call.args or (func_call.receiver and index == -1):
            return False
        overloaded_methods = self.api_graph.get_declarations_of_access(
            func_call, only_instance=False)
        if len(overloaded_methods) < 2:
            return False
        param_index = index
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

    def get_incompatible_type(self, candidate_t: tp.Type,
                              exp_t: tp.Type, loc) -> bool:
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
                                          exp_t: tp.Type,
                                          loc,
                                          type_con: tp.TypeConstructor,
                                          variances: Set[tp.Variance]):
        """
        This method produces the list of possible instantiations of a
        type parameter that has been previously instantiated with
        `type_arg`. This method considers the variance of the type parameter
        so that all the resulting instantiations are invalid.
        """
        instantiations = []
        if type_arg.is_wildcard():
            type_arg = type_arg.bound
        if type_con != self.bt_factory.get_array_type():
            # Wildcards
            instantiations.append(tp.WildCardType())
            if tp.Covariant in variances:
                instantiations.append(tp.WildCardType(type_arg,
                                                      tp.Contravariant))
            if tp.Contravariant in variances:
                instantiations.append(tp.WildCardType(type_arg, tp.Covariant))

        # Nested
        instantiations.append(exp_t)
        if type_arg.is_parameterized():
            instantiations.append(type_arg.type_args[0])
        candidate_types = self.get_representative_types(
            loc, type_arg, self._get_exclusion_strategies(variances),
            blacklisted_types={type_arg}
        )
        candidate_types = sorted(
            list(candidate_types),
            key=lambda x: type_similarity(x, exp_t, self.bt_factory),
            reverse=True
        )
        instantiations.extend(candidate_types)
        return instantiations

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

    def _gen_incompatible_parameterized_type_from_non_polymorphic_type(
        self,
        exp_t: tp.Type,
        type_con: tp.TypeConstructor
    ) -> tp.ParameterizedType:
        """
        This method instantiates the given type constructor `type_con` so
        that the resulting parameterized type is incompatible with `exp_t`.
        This parameterized type can be easily retrieved by freely
        instantiating the given type constructor.
        """
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
        sub: dict, loc
    ) -> List[dict]:
        types = self.api_graph.get_reg_types()
        reversed_sub = {}
        for k, v in sub.items():
            reversed_sub.setdefault(v, []).append(k)

        instantiations = {}
        type_var_assignments = exp_t.get_type_variable_assignments()
        indexes, default = {}, {}
        for i, (type_param, insts) in enumerate(reversed_sub.items()):
            indexes[i] = type_param
            t = utils.random.choice([
                tparam for tparam in insts
                if tparam in exp_t.t_constructor.type_parameters])
            if not t.is_type_var():
                instantiations.setdefault(type_param, []).append(t)
                default[type_param] = t
            else:
                type_arg = type_var_assignments[t]
                default[type_param] = type_arg
                variances = set()
                if (type_param.is_covariant() or
                        (type_arg.is_wildcard() and type_arg.variance.is_covariant())):
                    variances.add(tp.Covariant)

                if (type_param.is_contravariant() or
                        (type_arg.is_wildcard() and type_arg.is_contravariant())):
                    variances.add(tp.Contravariant)
                instantiations.setdefault(type_param, []).extend(
                    self.get_type_parameter_instantiations(
                        type_arg, exp_t, loc, type_con, variances))

        subs = []
        for i, (type_param, inst) in enumerate(instantiations.items()):
            for elem in inst:
                subs.append({
                    type_param: (
                        elem if indexes[i] == type_param
                        else default[type_param]
                    )
                    for k in reversed_sub.keys()
                })
        return subs

    def gen_incompatible_type_constructor_instantiations(
        self,
        exp_t: tp.Type,
        loc,
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
            # Example:
            # exp_t: List<String>, type_con: Map<String, Object>
            yield param_t[0]
            return

        # Here, the expected type and the type constructor have some sort
        # of relation. Try to instantiate the connected type parameters with
        # incompatible types. Example:
        # exp_t: List<String>, type_con: List<Integer>/LinkedList<Integer>.
        # In the above example, we make sure that List/LinkedList is not
        # instantiated with String.
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
                if v.is_type_constructor():
                    v = tu.instantiate_type_constructor(
                        v, types, only_regular=True,
                        rec_bound_handler=self.api_graph.get_instantiations_of_recursive_bound
                    )[0]
                type_args[index] = v
            yield type_con.new(type_args)


class NullIncompatibleTyping(IncompatibleTyping):
    def __init__(self, api_graph, bt_factory: BuiltinFactory):
        super().__init__(api_graph, bt_factory)

    def get_incompatible_type(self, candidate_t: tp.Type,
                              exp_t: tp.Type, loc) -> bool:
        """
        Given a candidate type t1 and an expected type t2, this method checks
        whether t1 is incompatible to t2.
        """
        is_nullable = (exp_t.is_parameterized() and
                       isinstance(exp_t.t_constructor, tp.NullableType))
        base_t = exp_t
        if is_nullable:
            base_t = exp_t.type_args[0]

        if is_nullable and not base_t.is_parameterized():
            return None
        if not is_nullable:
            if not candidate_t.is_type_constructor():
                if candidate_t.is_subtype(exp_t):
                    yield tp.NullableType().new([candidate_t])
                return

            t = candidate_t.new([tp.WildCardType()
                                 for _ in candidate_t.type_parameters])
            t2 = exp_t
            if exp_t.is_parameterized():
                t2 = exp_t.t_constructor.new(
                    [tp.WildCardType()
                     for _ in exp_t.t_constructor.type_parameters])
            if t.is_subtype(t2):
                yield from self.gen_incompatible_type_constructor_instantiations(
                    exp_t, loc, candidate_t
                )
        return None

    def gen_incompatible_type_constructor_instantiations(
        self,
        exp_t: tp.Type,
        loc,
        type_con: tp.TypeConstructor,
    ) -> Generator[tp.ParameterizedType, None, None]:
        """
        Given an expected type and a type constructor T, this function yields
        various instantiations of T so that the resulting parameterized type
        is not compatible with the given expected type.
        """
        types = self.api_graph.get_reg_types()
        param_t = tu.instantiate_type_constructor(
            type_con, types,
            only_regular=True,
            rec_bound_handler=self.api_graph.get_instantiations_of_recursive_bound
        )
        if param_t is None:
            return

        if not exp_t.is_parameterized():
            yield tp.NullableType().new([param_t[0]])
            return

        sub = tu.get_type_substitution_of_parent(exp_t, type_con)
        if not sub:
            # They types don't have connected type parameters. So, just return
            # an instantation of the candidate type constructor.
            # Example:
            # exp_t: List<String>, type_con: Map<String, Object>
            yield tp.NullableType().new([param_t[0]])
            return

        # Here, the expected type and the type constructor have some sort
        # of relation. Try to instantiate the connected type parameters with
        # incompatible types. Example:
        # exp_t: List<String>, type_con: List<Integer>/LinkedList<Integer>.
        # In the above example, we make sure that List/LinkedList is not
        # instantiated with String.
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
                if v.is_type_constructor():
                    v = tu.instantiate_type_constructor(
                        v, types, only_regular=True,
                        rec_bound_handler=self.api_graph.get_instantiations_of_recursive_bound
                    )[0]
                type_args[index] = v
            yield type_con.new(type_args)

    def get_type_parameter_instantiations(self, type_arg: tp.Type,
                                          exp_t: tp.Type,
                                          loc,
                                          type_con: tp.TypeConstructor,
                                          variances: Set[tp.Variance]):
        """
        This method produces the list of possible instantiations of a
        type parameter that has been previously instantiated with
        `type_arg`. This method considers the variance of the type parameter
        so that all the resulting instantiations are invalid.
        """
        instantiations = []
        base_type_arg = type_arg
        if type_arg.is_wildcard() and not type_arg.is_invariant():
            base_type_arg = type_arg.bound

        is_nullable = (base_type_arg.is_parameterized() and
                       isinstance(base_type_arg.t_constructor,
                                  tp.NullableType))

        new_type_arg = (base_type_arg.type_args[0]
                        if is_nullable
                        else tp.NullableType().new([base_type_arg]))

        if type_con != self.bt_factory.get_array_type() and \
                type_arg.is_wildcard():
            # Wildcards
            if tp.Covariant in variances and not is_nullable:
                instantiations.append(
                    tp.WildCardType(
                        new_type_arg,
                        tp.Covariant
                    )
                )
            if tp.Contravariant in variances and is_nullable:
                instantiations.append(
                    tp.WildCardType(
                        new_type_arg,
                        tp.Contravariant
                    )
                )

        if _add_base_type_arg(is_nullable, variances):
            instantiations.append(new_type_arg)
        exclusion_strategies = None
        if tp.Covariant in variances and not is_nullable:
            exclusion_strategies = {self.ONLY_SUBTYPES}
            blacklisted_types = {base_type_arg}
            base_t = base_type_arg
        if tp.Contravariant in variances and is_nullable:
            exclusion_strategies = {self.ONLY_SUPERTYPES}
            blacklisted_types = {base_type_arg.t_constructor}
            base_t = base_type_arg.type_args[0]
        if exclusion_strategies is not None:
            candidate_types = self.get_representative_types(
                loc, base_t, exclusion_strategies,
                blacklisted_types=blacklisted_types
            )
            candidate_types = sorted(
                list(candidate_types),
                key=lambda x: type_similarity(x, exp_t, self.bt_factory),
                reverse=True
            )
            instantiations.extend([
                t if is_nullable else tp.NullableType().new([t])
                for t in candidate_types
            ])
        return instantiations
