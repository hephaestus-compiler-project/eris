from collections import defaultdict
from typing import Dict, Set, List

from src import utils
from src.ir import types as tp, type_utils as tu, ast
from src.ir.builtins import BuiltinFactory
from src.generators.api import api_graph as ag, utils as au


def get_arg_api(arg):
    if isinstance(arg.expr, ast.Lambda):
        # The argument is a lambda expression. We need its signature.
        return arg.expr.signature
    return arg.path[-2]


class TypeEraser():
    OUT = -1

    def __init__(self, api_graph: ag.APIGraph,
                 bt_factory: BuiltinFactory,
                 inject_error_mode: bool):
        self.api_graph = api_graph
        self.bt_factory = bt_factory
        self.inject_error_mode = inject_error_mode
        # This is used for maintaining a stack of expected types used for
        # determining the expected types of the generated expressions.
        # This is used for type erasure.
        self.expected_types = []

    @property
    def expected_type(self):
        if not self.expected_types:
            return None
        return self.expected_types[-1]

    def with_target(self, target_type, type_variables=None):
        self.expected_types.append((target_type, type_variables or []))

    def reset_target_type(self):
        if self.expected_types:
            self.expected_types = self.expected_types[:-1]

    def get_api_output_type(self, api: ag.APINode) -> tp.Type:
        if isinstance(api, tp.Type):
            # Fill parameterized type with type parameters.
            new_type_args = api.t_constructor.type_parameters
            return self.bt_factory.get_function_type(
                len(new_type_args) - 1).new(new_type_args)
        return self.api_graph.get_concrete_output_type(api)

    def get_type_parameters(self, api: ag.APINode) -> List[tp.TypeParameter]:
        if isinstance(api, ag.Constructor):
            t = self.api_graph.get_type_by_name(api.get_class_name())
            if t.is_type_constructor():
                return t.type_parameters
            else:
                return []
        if isinstance(api, tp.Type):
            # In case of lambda we exclude the last type parameter which can
            # be inferred by its body.
            return api.t_constructor.type_parameters[:-1]
        if isinstance(api, ag.Method):
            return api.type_parameters
        return []

    def compute_markings(self,
                         api: ag.APINode) -> Dict[tp.TypeParameter, Set[int]]:
        markings = defaultdict(set)
        ret_type = self.get_api_output_type(api)
        for type_param in self.get_type_parameters(api):
            if isinstance(api, (ag.Constructor, tp.Type)):
                # All type parameters of a constructor or a function type are
                # in out position.
                markings[type_param].add(self.OUT)
            else:
                # Check the return type of polymorphic function.
                type_variables = tu.get_type_variables_of_type(
                    ret_type, self.bt_factory)
                if type_param in type_variables:
                    markings[type_param].add(self.OUT)

            for i, param in enumerate(getattr(api, "parameters", [])):
                type_variables = tu.get_type_variables_of_type(param.t,
                                                               self.bt_factory)
                if type_param in type_variables:
                    markings[type_param].add(i)
        return markings

    def can_infer_out_position(self, type_param: tp.TypeParameter,
                               marks: Set[int], api_out_type: tp.Type) -> bool:
        target_type, type_vars = self.expected_type
        if self.OUT not in marks or target_type is None:
            return False

        if marks == {self.OUT} and self.inject_error_mode:
            return False

        return target_type == api_out_type or bool(
            tu.unify_types(target_type, api_out_type, self.bt_factory,
                           same_type=False, subtype_on_left=False))

    def can_infer_in_position(self, type_param: tp.TypeParameter,
                              marks: Set[int], api_params: List[tp.Type],
                              api_args: List[ag.APIPath]) -> bool:
        can_infer = False
        for mark in marks.difference({self.OUT}):
            arg = api_args[mark]
            if len(arg.path) == 1 and not isinstance(arg.expr, ast.Lambda):
                # This means that we have a concrete type.
                return True

            arg_api = get_arg_api(arg)
            type_parameters = self.get_type_parameters(arg_api)
            if not type_parameters:
                # The argument is not a polymorphic call. We can infer
                # the argument type without a problem.
                return True

            arg_type = self.get_api_output_type(arg_api)
            type_variables = tu.get_type_variables_of_type(arg_type,
                                                           self.bt_factory)
            method_type_params = {
                tpa for tpa in type_parameters
                if tpa in type_variables
            }
            expected_param_type = \
                self.api_graph.get_functional_type_instantiated(
                    api_params[mark].t) or api_params[mark].t
            sub = tu.unify_types(expected_param_type, arg_type,
                                 self.bt_factory, same_type=False)
            if not sub:
                continue
            for mtpa in method_type_params:
                if any(mtpa in tu.get_type_variables_of_type(p.t,
                                                             self.bt_factory)
                       for p in getattr(arg_api, "parameters", [])):
                    # Type variable of API is in "in" position.
                    can_infer = True
                    continue
                if not sub[mtpa].is_type_var():
                    can_infer = True
        return can_infer

    def erase_var_type(self, var_decl, expr_res):
        def get_expr_type(expr_res):
            expr_type = expr_res.path[-1]
            if expr_type.is_type_constructor():
                return expr_type.new([expr_res.type_var_map[tpa]
                                      for tpa in expr_type.type_parameters])
            return tp.substitute_type(expr_type, expr_res.type_var_map)

        if (self.inject_error_mode and
                var_decl.get_type().is_parameterized() and
                var_decl.get_type().has_wildcards()):
            return

        expr = expr_res.expr
        if isinstance(expr, (ast.Lambda, ast.FunctionReference)):
            # We don't erase the target type of lambdas and function
            # references
            return

        path = expr_res.path
        expr_type = get_expr_type(expr_res)
        if expr_type.name != var_decl.get_type().name:
            # The type of the expression has a different type from the
            # explicit type of the variable. We are conservative; we cannot
            # erase.
            return

        if len(path) == 1:
            var_decl.omit_type()
            return

        api = get_arg_api(expr_res)
        if getattr(api, "metadata", {}).get("is_special", False):
            return
        type_parameters = self.get_type_parameters(api)
        if not type_parameters:
            # The API is not polymorphic, so we are free to omit the variable
            # type.
            var_decl.omit_type()
            return

        # Now, check if the output type of the API contains type variables
        # defined inside API, e.g., fun <T> m(): T
        expr_type = self.get_api_output_type(api)
        type_vars = tu.get_type_variables_of_type(expr_type, self.bt_factory)
        api_type_params = {
            tpa for tpa in type_parameters
            if tpa in type_vars
        }
        if not api_type_params or all(
                tpa in tu.get_type_variables_of_type(p.t, self.bt_factory)
                for tpa in api_type_params
                for p in getattr(api, "parameters", [])):
            var_decl.omit_type()

    def erase_types(self, expr: ast.Expr, api: ag.APINode,
                    args: List[ag.APIPath]):
        if getattr(api, "metadata", {}).get("is_special", False):
            return
        if isinstance(api, (ag.Method, ag.Constructor)):
            # Checks whether erasing type arguments from polymorphic call
            # creates ambiguity issues.
            overloaded_methods = self.api_graph.get_overloaded_methods(
                self.api_graph.get_input_type(api), api)
            typing_seq = [p.path[-1] for p in args]
            if any(au.is_typing_seq_ambiguous(api, m, typing_seq)
                   for m, _ in overloaded_methods):
                return

        markings = self.compute_markings(api)
        omittable_type_params = set()
        ret_type = self.get_api_output_type(api)
        for type_param, marks in markings.items():
            if self.can_infer_out_position(type_param, marks, ret_type):
                omittable_type_params.add(type_param)
                continue
            if self.can_infer_in_position(type_param, marks,
                                          getattr(api, "parameters", []),
                                          args):
                omittable_type_params.add(type_param)
        if len(omittable_type_params) == len(self.get_type_parameters(api)):
            expr.omit_types()

    def _can_omit_with_expected_type(self, expr: ast.Expr,
                                     api: ag.APINode, sub: dict):
        exp_t = expr.get_type_info()[0]
        if exp_t is None:
            return False

        output_type = tp.substitute_type(
            self.get_api_output_type(api), sub)
        return not output_type.is_subtype(exp_t)

    def _get_inferred_sub(self, expr: ast.Expr, api: ag.APINode,
                          new_type: tp.Type, index: int):
        # We get the type substitution of the receiver, in case any method's
        # type parameter is bounded by a class type parameter.
        instance_sub = {}
        is_receiver_loc = index == -1
        func_sub = {type_param: expr.type_args[i]
                    for i, type_param in enumerate(api.type_parameters)}
        if getattr(expr, "receiver", None):
            if is_receiver_loc:
                assert new_type.is_parameterized()
                instance_sub = new_type.get_type_variable_assignments()
            elif expr.receiver.is_typed():
                rec_type = expr.receiver.get_type_info()[1]
                if rec_type.is_parameterized():
                    instance_sub = rec_type.get_type_variable_assignments()

        # arg_val = expr.args[index]
        # if not arg_val.expr.is_typed():
        #     expr.recover_types()
        #     return False
        if not is_receiver_loc:
            arg_types = [
                arg.expr.get_type_info()[1] if i != index else new_type
                for i, arg in enumerate(expr.args)
            ]
            new_sub = au._infer_sub_for_method(api, arg_types)
            if new_sub is None:
                new_sub = {
                    type_param: (self.bt_factory.get_any_type()
                                 if type_param.bound is None
                                 else tp.substitute_type(type_param.bound,
                                                         instance_sub))
                    for type_param in api.type_parameters
                }
            return {k: v for k, v in new_sub.items()
                    if k in api.type_parameters}
        else:
            instance_sub.update(func_sub)
            return instance_sub

    def _get_actual_sub(self, expr: ast.Expr, api: ag.APINode) -> dict:
        sub = {}
        receiver = getattr(expr, "receiver", None)
        if receiver and receiver.is_typed():
            receiver_t = receiver.get_type_info()[1]
            sub.update(receiver_t.get_type_variable_assignments())
        # This is the actual type substitution of the polymophic method call
        sub.update({type_param: expr.type_args[i]
                    for i, type_param in enumerate(api.type_parameters)})
        return sub

    def _get_type_variables_of_api(self, api: ag.APINode, index: int):
        is_receiver_loc = index == -1
        type_vars = set()
        if is_receiver_loc:
            for param in api.parameters:
                type_vars.update(tu.get_type_variables_of_type(param.t))
        else:
            type_vars.update(tu.get_type_variables_of_type(
                api.parameters[index].t))
        return type_vars

    def erase_types_ill_typed(self, expr: ast.Expr, api: ag.APINode,
                              new_type: tp.Type, index: int,
                              parents: List[ast.Node]):
        if not isinstance(expr, (ast.FunctionCall, ast.New)):
            return False

        is_receiver_loc = index == -1

        # markings = self.compute_markings(api)
        # Get the type variables that touch the affected parameter.
        type_vars = self._get_type_variables_of_api(api, index)
        if not type_vars and not is_receiver_loc:
            # If there are not affected type variables, then we can omit
            # type arguments freely.
            expr.omit_types()
            return True

        valid_sub = self._get_actual_sub(expr, api)
        new_sub = self._get_inferred_sub(expr, api, new_type, index)
        ill_typed = False
        for i, arg in enumerate(expr.args):
            actual_arg_type = arg.expr.get_type_info()[1]
            if i == index:
                actual_arg_type = new_type
            expected_type = tp.substitute_type(api.parameters[i].t,
                                               new_sub)
            if not actual_arg_type.is_subtype(expected_type):
                ill_typed = True
                break
        if ill_typed:
            expr.omit_types()
            return True
        else:
            if not parents:
                expr.recover_types()
                return False
            while parents:
                parent, parent_index = parents[0]
                if isinstance(parent, (ast.Assignment, ast.FunctionDeclaration,
                                       ast.VariableDeclaration)):
                    if self._can_omit_with_expected_type(expr, api, new_sub):

                        if isinstance(parent, ast.VariableDeclaration):
                            if utils.random.bool():
                                expr.omit_types()
                                parent.recover_type()
                            else:
                                parent.omit_type()
                                expr.recover_types()
                        else:
                            expr.omit_types()
                        return True

                if isinstance(parent, ast.FunctionCall):
                    decl = self.api_graph.find_applicable_method(
                        parent, self.api_graph.get_declarations_of_access(
                            parent, only_instance=False))
                    out_type = self.api_graph.get_concrete_output_type(api)
                    new_t = tp.substitute_type(out_type, new_sub)
                    actual_t = tp.substitute_type(out_type, valid_sub)
                    if is_receiver_loc and not new_t.is_subtype(actual_t):
                        expr.omit_types()
                        return True
                    # if not parent.type_args:
                    #     expr.omit_types()
                    #     return True
                    res = self.erase_types_ill_typed(parent, decl, new_t,
                                                     parent_index, parents[1:])
                    if not res:
                        if parent_index != -1:
                            expr.omit_types()
                            return True
                    else:
                        expr.omit_types()
                        return True
                if isinstance(parent, ast.Conditional) and parent_index == 0:
                    expr.recover_types()
                    return False
                if isinstance(parent, ast.Conditional) and parent_index != 0:
                    parents = parents[1:]

                elif isinstance(parent, ast.Block):
                    parents = parents[1:]
                else:
                    break

        expr.recover_types()
        return False
