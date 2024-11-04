from typing import Set, Dict

from src.enumerators.analyses import Loc, ExprLocationAnalysis
from src.enumerators.error import ErrorEnumerator
from src.enumerators.utils import NullIncompatibleTyping, IncompatibleTyping
from src.enumerators import type_abstractions as ta
from src.ir import ast, type_utils as tu, types as tp
from src.ir.builtins import BuiltinFactory
from src.ir.visitors import ASTExprUpdate
from src.generators import Generator as ProgramGenerator
from src.generators.api import nodes


class TypeErrorEnumerator(ErrorEnumerator):
    name = "TypeErrorEnumerator"

    OUT_POS = -1

    def __init__(self, program: ast.Program, program_gen: ProgramGenerator,
                 bt_factory: BuiltinFactory, options: dict = None):
        self.locations = []
        self.api_graph = program_gen.api_graph
        self.error_loc = None
        self.new_node = None
        self.depth = 0
        self.analysis = ExprLocationAnalysis()
        self.options = options
        self.cache = set()
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

    def reconstruct_scope(self, loc: Loc):
        self.api_graph.add_types(list(loc.scope["local_types"].values()))
        for var_name, var_type in loc.scope["local_vars"].items():
            if isinstance(var_type, str):
                var_type = self.api_graph.get_type_by_name(var_type)
            self.api_graph.add_variable_node(var_name, var_type)

    def delete_scope(self, loc: Loc):
        self.api_graph.remove_types(list(loc.scope["local_types"].values()))
        for var_name in loc.scope["local_vars"].keys():
            self.api_graph.remove_variable_node(var_name)

    def get_candidate_program_locations(self):
        self.analysis.visit_program(self.program)
        return self.analysis.locations

    def filter_program_locations(self, locations):
        filtered_locs = []
        for elem, parent, index, depth, scope in locations:
            if not elem.is_typed():
                continue
            exp_t, actual_t = elem.get_type_info()
            t = exp_t
            if t in [
                    self.bt_factory.get_any_type(),
                    self.bt_factory.get_void_type(primitive=True),
                    self.bt_factory.get_void_type(primitive=False)
            ]:
                continue
            if exp_t is not None:
                new_t = ta.to_type_abstraction(exp_t, self.bt_factory)
            else:
                new_t = None
            cached_elem = (type(parent), new_t, depth, index,
                           tuple(scope["local_types"].values()))
            if cached_elem not in self.cache:
                filtered_locs.append(Loc(elem, parent, index, depth, scope))
                self.cache.add(cached_elem)

        return filtered_locs

    def get_programs_with_error(self, loc):
        exp_t, actual_t = loc.expr.get_type_info()
        try:
            self.reconstruct_scope(loc)
            for incompatible_t in self.enumerate_incompatible_typings(loc):
                self.program_gen.block_variables = True
                if self.program_gen.type_eraser:
                    self.program_gen.type_eraser.inject_error_mode = True
                    self.program_gen.type_eraser.with_target(
                        loc.get_parent_expected_type(self.api_graph))
                expr = self.program_gen._generate_expr_from_node(
                    incompatible_t, depth=1)
                if self.program_gen.type_eraser:
                    self.program_gen.type_eraser.inject_error_mode = False
                    self.program_gen.type_eraser.reset_target_type()
                expr.expr.mk_typed(ast.TypePair(expected=exp_t,
                                                actual=incompatible_t))
                if isinstance(expr.expr, (ast.FunctionCall, ast.New)):
                    decl = self.api_graph.get_declaration_of_access(
                            expr.expr, only_instance=False)
                self.program_gen.block_variables = False
                if self.program_gen.type_eraser:
                    if loc.is_parent_call():
                        decl = self.api_graph.get_declaration_of_access(
                                loc.parent, only_instance=False)
                        parents = self.analysis.get_parents(loc.parent)
                        if decl and getattr(loc.parent, "args", None):
                            self.program_gen.type_eraser.erase_types_ill_typed(
                                loc.parent, decl, incompatible_t, loc.index,
                                parents
                            )
                    if loc.is_parent_var_decl():
                        loc.parent.recover_type()
                upd = ASTExprUpdate(loc.index, expr.expr)
                upd.visit(loc.parent)
                self.add_err_message(loc, expr.expr)
                yield self.program
            self.delete_scope(loc)
        except Exception as e:
            self.program_gen.block_variables = False
            self.delete_scope(loc)
            raise e
        return None

    def enumerate_incompatible_typings(self, loc):
        if loc.is_receiver_loc():
            # For receiver expression, we have a different logic to enumerate
            # the incompatible typings.
            yield from self.get_incompatible_type_of_receiver(loc)
            return
        exp_t, _ = loc.expr.get_type_info()
        typer = (
            NullIncompatibleTyping(self.api_graph, self.bt_factory)
            if self.options.get("use-nullable-types", False)
            else IncompatibleTyping(self.api_graph, self.bt_factory)
        )
        yield from typer.enumerate_incompatible_typings(exp_t, loc)

    def get_type_variables_of_node_signature(self, node: nodes.APINode,
                                             receiver_type: tp.Type):
        """
        Given a particular node in the API graph, this method takes all the
        type variables that are found in its signature.
        """
        type_variables = {}
        parent = self.api_graph.get_type_by_name(node.cls)
        assert parent is not None
        sub = tu.get_type_substitution_of_parent(parent, receiver_type)
        if isinstance(node, (nodes.Method, nodes.Constructor)):
            for i, p in enumerate(node.parameters):
                type_vars = tu.get_type_variables_of_type(
                    tp.substitute_type(p.t, sub))
                for t in type_vars:
                    type_variables.setdefault(t, set()).add(i)
        out_type = self.api_graph.get_concrete_output_type(node)
        type_vars = tu.get_type_variables_of_type(
            tp.substitute_type(out_type, sub))
        for t in type_vars:
            type_variables.setdefault(t, set()).add(self.OUT_POS)
        return type_variables

    def is_replacement_valid(self, parents: list) -> bool:
        def is_assignment_valid(t: tp.Type) -> bool:
            t = (
                t.get_bound_rec(self.bt_factory)
                if t.is_type_var()
                else t
            )
            return not t.is_nullable()

        for parent, index in parents:
            if isinstance(parent, ast.VariableDeclaration):
                return is_assignment_valid(parent.inferred_type)
            if isinstance(parent, ast.FunctionDeclaration):
                return is_assignment_valid(parent.get_type())

            if isinstance(parent, ast.FunctionCall):
                if index >= 0:
                    exp_t = parent.args[index].expr.get_type_info()[0]
                    return is_assignment_valid(exp_t)
        return True

    def replace_receiver_type(self, loc: Loc, receiver_type: tp.Type,
                              type_variables: Dict[tp.TypeParameter, Set[int]]):
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
            if v in type_variables.keys():
                mapped_type_vars.setdefault(v, set()).add(k.variance)
        parents = self.analysis.get_parents(loc.parent)
        # We follow a conservative approach and we recover the types of
        # the parents corresponding to variable declaration. We avoid
        # situations like the following:
        #
        # val x: List<Int>().get()
        # val x: List<Any>().get() -> this makes the programm well-typed
        # val x: Int = List<Any>().get() -> this is the correct one
        for p, _ in parents:
            if isinstance(p, ast.VariableDeclaration):
                p.recover_type()
        typer = (
            NullIncompatibleTyping(self.api_graph, self.bt_factory)
            if self.options.get("use-nullable-types", False)
            else IncompatibleTyping(self.api_graph, self.bt_factory)
        )

        for type_var, positions in type_variables.items():
            type_arg = sub[type_var]
            # Case: val x: Any = List<Any>().get(0)
            # Any substitution of List<Any>() retains the validity of
            # the program.
            if positions == {self.OUT_POS} and type_arg.name in [
                    self.bt_factory.get_any_type().name,
                    self.bt_factory.get_string_type().name,
                    self.bt_factory.get_boolean_type().name
            ]:
                continue
            if self.OUT_POS in positions:
                if not self.is_replacement_valid(parents):
                    continue
                mapped_type_vars.setdefault(type_var, set()).add(tp.Covariant)
            if positions.difference({self.OUT_POS}):
                mapped_type_vars.setdefault(type_var, set()).add(
                    tp.Contravariant
                )
            if type_arg == self.bt_factory.get_void_type(primitive=False):
                continue
            for new_type_arg in typer.get_type_parameter_instantiations(
                    type_arg, receiver_type, loc, type_con,
                    mapped_type_vars[type_var]):
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

    def get_incompatible_type_of_receiver(self, loc: Loc):
        """
        Based on a given location that corresponds to a receiver expression,
        this method produces a type that is an forms an incompatible receiver.
        """
        assert loc.parent.receiver is not None, (
            "Assertion failed: parent location does not contain a receiver")
        use_nullables = self.options.get("use-nullable-types", False)
        decl = self.api_graph.get_declaration_of_access(loc.parent)
        receiver_type = loc.parent.receiver.get_type_info()[1]
        if not receiver_type.is_parameterized():
            if use_nullables:
                typer = NullIncompatibleTyping(self.api_graph, self.bt_factory)
                yield from typer.enumerate_incompatible_typings(receiver_type,
                                                                loc)
            return

        # Receiver is polymorphic
        type_variables = self.get_type_variables_of_node_signature(
            decl, receiver_type)
        type_variables = {k: v for k, v in type_variables.items()
                          if k in receiver_type.t_constructor.type_parameters}
        if use_nullables:
            yield tp.NullableType().new([receiver_type])
        if not type_variables:
            return
        yield from self.replace_receiver_type(loc, receiver_type,
                                              type_variables)
