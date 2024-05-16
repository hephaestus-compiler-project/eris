from src.ir import ast
from src.ir.visitors import DefaultVisitor


class ProgramUpdate(DefaultVisitor):
    def __init__(self, index: int, new_node: ast.Node):
        self.index = index
        self.new_node = new_node

    def visit_block(self, node):
        body = node.body
        if len(body) > self.idex:
            return
        body[self.index] = self.new_node

    def visit_var_decl(self, node):
        node.expr = self.new_node

    def visit_func_decl(self, node):
        if isinstance(node.body, ast.Expr):
            node.body = self.new_node

    def visit_lambda(self, node):
        if isinstance(node.body, ast.Expr):
            node.body = self.new_node

    def visit_func_ref(self, node):
        node.receiver = self.new_node

    def visit_array_expr(self, node):
        if len(node.exprs) > self.index:
            return
        node.exprs[self.index] = self.new_node

    def visit_logical_expr(self, node):
        if self.index == 0:
            node.lexpr = self.new_node
        else:
            node.rexpr = self.new_node

    def visit_equality_expr(self, node):
        if self.index == 0:
            node.lexpr = self.new_node
        else:
            node.rexpr = self.new_node

    def visit_comparison_expr(self, node):
        if self.index == 0:
            node.lexpr = self.new_node
        else:
            node.rexpr = self.new_node

    def visit_arith_expr(self, node):
        if self.index == 0:
            node.lexpr = self.new_node
        else:
            node.rexpr = self.new_node

    def visit_conditional(self, node):
        if self.index == 0:
            node.cond = self.new_node
        elif self.index == 1:
            node.true_branch = self.new_node
        else:
            node.false_branch = self.new_node

    def visit_new(self, node):
        if len(node.args) > self.index:
            return
        node.args[self.index] = self.new_node

    def visit_field_access(self, node):
        node.receiver = self.new_node

    def visit_func_call(self, node):
        if node.receiver:
            if self.index == 0:
                node.receiver = self.new_node
            else:
                node.args[self.index - 1] = self.new_node
        else:
            node.args[self.index] = self.new_node

    def visit_assign(self, node):
        if node.receiver:
            if self.index == 0:
                node.receiver = self.new_node
            else:
                node.expr = self.new_node
        else:
            node.expr = self.new_node
