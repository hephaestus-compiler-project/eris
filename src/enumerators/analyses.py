from copy import deepcopy
from typing import NamedTuple, List, Tuple

from src.ir import ast, types as tp
from src.ir.visitors import DefaultVisitor
from src.generators.api import nodes


class Loc(NamedTuple):
    expr: ast.Expr
    parent: ast.Node
    index: int
    depth: int
    scope: dict

    def is_receiver_loc(self):
        if isinstance(self.parent, (ast.FieldAccess, ast.FunctionReference)):
            return True
        if (isinstance(self.parent, ast.FunctionCall)
                and self.parent.receiver is not None
                and self.index == 0):
            return True

        if (isinstance(self.parent, ast.Assignment)
                and self.parent.receiver is not None
                and self.index == 0):
            return True
        return False


class LocationAnalysis(DefaultVisitor):
    def __init__(self):
        self.locations: List[Loc] = []


class ExprLocationAnalysis(LocationAnalysis):
    def __init__(self):
        super().__init__()
        self.depth = 0
        self.namespace = tuple()
        self.scope = {
            "local_vars": {},
            "local_types": {},
        }

    def push_local_var(self, var_name: str, var_type):
        self.scope["local_vars"][var_name] = var_type

    def push_local_type(self, t: tp.Type):
        self.scope["local_types"][t.name] = t

    def pop_local_var(self, var_name: str):
        del self.scope["local_vars"][var_name]

    def pop_local_type(self, type_name: str):
        del self.scope["local_types"][type_name]

    def visit_class_decl(self, node):
        prev_namespace = self.namespace
        self.namespace += (node.name,)
        for type_param in node.type_parameters:
            self.push_local_type(type_param)
        super().visit_class_decl(node)
        for type_param in node.type_parameters:
            self.pop_local_type(type_param.name)
        self.namespace = prev_namespace

    def visit_block(self, node):
        super().visit_block(node)
        for i, elem in enumerate(node.body):
            if isinstance(elem, ast.Expr):
                self.locations.append(Loc(elem, node, i, self.depth,
                                          deepcopy(self.scope)))

    def visit_var_decl(self, node):
        prev_depth = self.depth
        self.depth += 1
        super().visit_var_decl(node)
        self.depth = prev_depth
        self.locations.append(Loc(node.expr, node, 0, self.depth,
                                  deepcopy(self.scope)))

    def visit_func_decl(self, node):
        if not node.metadata.get("is_static"):
            self.push_local_var("this", self.namespace[-1])
        for p in node.params:
            self.push_local_var(p.name, p.get_type())
        for type_param in node.type_parameters:
            self.push_local_type(type_param)

        prev_depth = self.depth
        self.depth += 1
        super().visit_func_decl(node)
        self.depth = prev_depth
        if node.body and isinstance(node.body, ast.Expr):
            self.locations.append(Loc(node.body, node, 0, self.depth,
                                      deepcopy(self.scope)))
        if not node.metadata.get("is_static"):
            self.pop_local_var("this")
        for p in node.params:
            self.pop_local_var(p.name)
        for type_param in node.type_parameters:
            self.pop_local_type(type_param.name)

    def visit_lambda(self, node):
        for p in node.params:
            self.push_local_var(p.name, p.get_type())
        prev_depth = self.depth
        self.depth += 1
        super().visit_lambda(node)
        self.depth = prev_depth
        if isinstance(node.body, ast.Expr):
            self.locations.append(Loc(node.body, node, 0, self.depth,
                                      deepcopy(self.scope)))
        for p in node.params:
            self.pop_local_var(p.name)

    def visit_func_ref(self, node):
        prev_depth = self.depth
        self.depth += 1
        super().visit_func_ref(node)
        self.depth = prev_depth
        if node.receiver:
            self.locations.append(Loc(node.receiver, node, 0, self.depth,
                                      deepcopy(self.scope)))

    def visit_array_expr(self, node):
        prev_depth = self.depth
        self.depth += 1
        super().visit_array_expr(node)
        self.depth = prev_depth
        for i, expr in enumerate(node.exprs):
            self.locations.append(Loc(expr, node, i, self.depth,
                                      deepcopy(self.scope)))

    def visit_binary_expr(self, node):
        prev_depth = self.depth
        self.depth += 1
        super().visit_binary_expr(node)
        self.depth = prev_depth
        self.locations.append(Loc(node.lexpr, node, 0, self.depth,
                                  deepcopy(self.scope)))
        self.locations.append(Loc(node.rexpr, node, 1, self.depth,
                                  deepcopy(self.scope)))

    def visit_logical_expr(self, node):
        prev_depth = self.depth
        self.depth += 1
        super().visit_logical_expr(node)
        self.depth = prev_depth
        self.locations.append(Loc(node.lexpr, node, 0, self.depth,
                                  deepcopy(self.scope)))
        self.locations.append(Loc(node.rexpr, node, 1, self.depth,
                                  deepcopy(self.scope)))

    def visit_equality_expr(self, node):
        prev_depth = self.depth
        self.depth += 1
        super().visit_equality_expr(node)
        self.depth = prev_depth
        self.locations.append(Loc(node.lexpr, node, 0, self.depth,
                                  deepcopy(self.scope)))
        self.locations.append(Loc(node.rexpr, node, 1, self.depth,
                                  deepcopy(self.scope)))

    def visit_comparison_expr(self, node):
        prev_depth = self.depth
        self.depth += 1
        super().visit_comparison_expr(node)
        self.depth = prev_depth
        self.locations.append(Loc(node.lexpr, node, 0, self.depth,
                                  deepcopy(self.scope)))
        self.locations.append(Loc(node.rexpr, node, 1, self.depth,
                                  deepcopy(self.scope)))

    def visit_arith_expr(self, node):
        prev_depth = self.depth
        self.depth += 1
        super().visit_arith_expr(node)
        self.depth = prev_depth
        self.locations.append(Loc(node.lexpr, node, 0, self.depth,
                                  deepcopy(self.scope)))
        self.locations.append(Loc(node.rexpr, node, 1, self.depth,
                                  deepcopy(self.scope)))

    def visit_conditional(self, node):
        prev_depth = self.depth
        self.depth += 1
        super().visit_conditional(node)
        self.depth = prev_depth
        self.locations.append(Loc(node.cond, node, 0, self.depth,
                                  deepcopy(self.scope)))
        if isinstance(node.true_branch, ast.Expr):
            self.locations.append(Loc(node.true_branch, node, 1, self.depth,
                                      deepcopy(self.scope)))
        if isinstance(node.false_branch, ast.Expr):
            self.locations.append(Loc(node.false_branch, node, 2, self.depth,
                                      deepcopy(self.scope)))

    def visit_new(self, node):
        prev_depth = self.depth
        self.depth += 1
        super().visit_new(node)
        self.depth = prev_depth
        for i, e in enumerate(node.args):
            self.locations.append(Loc(e, node, i, self.depth,
                                      deepcopy(self.scope)))

    def visit_field_access(self, node):
        prev_depth = self.depth
        self.depth += 1
        super().visit_field_access(node)
        self.depth = prev_depth
        if node.expr:
            self.locations.append(Loc(node.expr, node, 0, self.depth,
                                      deepcopy(self.scope)))

    def visit_func_call(self, node):
        prev_depth = self.depth
        self.depth += 1
        super().visit_func_call(node)
        self.depth = prev_depth
        j = 0
        if node.receiver:
            self.locations.append(Loc(node.receiver, node, 0, self.depth,
                                      deepcopy(self.scope)))
            j = 1
        for i, p in enumerate(node.args):
            if isinstance(p, ast.CallArgument):
                self.locations.append(Loc(p.expr, node, i + j, self.depth,
                                          deepcopy(self.scope)))
            else:
                self.locations.append(Loc(p, node, i + j, self.depth,
                                          deepcopy(self.scope)))

    def visit_assign(self, node):
        prev_depth = self.depth
        self.depth += 1
        super().visit_assign(node)
        self.depth = prev_depth
        j = 0
        if node.receiver:
            self.locations.append(Loc(node.receiver, node, j, self.depth,
                                      deepcopy(self.scope)))
            j = 1
        self.locations.append(Loc(node.expr, node, j, self.depth,
                                  deepcopy(self.scope)))
