from collections import OrderedDict

from src.ir import ast
from src.ir import types


class ASTVisitor():

    def result(self):
        raise NotImplementedError('result() must be implemented')

    def visit(self, node):
        visitors = {
            ast.SuperClassInstantiation: self.visit_super_instantiation,
            ast.ClassDeclaration: self.visit_class_decl,
            types.TypeParameter: self.visit_type_param,
            types.TypeParameterConstructor: self.visit_type_param,
            ast.CallArgument: self.visit_call_argument,
            ast.FieldDeclaration: self.visit_field_decl,
            ast.VariableDeclaration: self.visit_var_decl,
            ast.ParameterDeclaration: self.visit_param_decl,
            ast.Constructor: self.visit_constructor,
            ast.FunctionDeclaration: self.visit_func_decl,
            ast.Lambda: self.visit_lambda,
            ast.FunctionReference: self.visit_func_ref,
            ast.BottomConstant: self.visit_bottom_constant,
            ast.NullConstant: self.visit_null_constant,
            ast.IntegerConstant: self.visit_integer_constant,
            ast.RealConstant: self.visit_real_constant,
            ast.CharConstant: self.visit_char_constant,
            ast.StringConstant: self.visit_string_constant,
            ast.ArrayExpr: self.visit_array_expr,
            ast.BooleanConstant: self.visit_boolean_constant,
            ast.Variable: self.visit_variable,
            ast.UnaryExpr: self.visit_unary_expr,
            ast.BinaryExpr: self.visit_binary_expr,
            ast.LogicalExpr: self.visit_logical_expr,
            ast.EqualityExpr: self.visit_equality_expr,
            ast.ComparisonExpr: self.visit_comparison_expr,
            ast.ArithExpr: self.visit_arith_expr,
            ast.Conditional: self.visit_conditional,
            ast.MultiConditional: self.visit_multiconditional,
            ast.Is: self.visit_is,
            ast.New: self.visit_new,
            ast.FieldAccess: self.visit_field_access,
            ast.FunctionCall: self.visit_func_call,
            ast.Assignment: self.visit_assign,
            ast.Program: self.visit_program,
            ast.Block: self.visit_block,
            ast.TryCatch: self.visit_trycatch,
            ast.Return: self.visit_return,
            ast.Loop: self.visit_loop,
        }
        visitor = visitors.get(node.__class__)
        if visitor is None:
            raise Exception(
                "Cannot find visitor for instance node " + str(node.__class__))
        return visitor(node)

    def visit_program(self, node):
        raise NotImplementedError('visit_program() must be implemented')

    def visit_block(self, node):
        raise NotImplementedError('visit_block() must be implemented')

    def visit_super_instantiation(self, node):
        raise NotImplementedError(
            'visit_super_instantiation() must be implemented')

    def visit_class_decl(self, node):
        raise NotImplementedError('visit_class_decl() must be implemented')

    def visit_type_param(self, node):
        raise NotImplementedError('visit_type_param() must be implemented')

    def visit_var_decl(self, node):
        raise NotImplementedError('visit_var_decl() must be implemented')

    def visit_call_argument(self, node):
        raise NotImplementedError('visit_call_argument() must be implemented')

    def visit_field_decl(self, node):
        raise NotImplementedError('visit_field_decl() must be implemented')

    def visit_param_decl(self, node):
        raise NotImplementedError('visit_param_decl() must be implemented')

    def visit_constructor(self, node):
        raise NotImplementedError('visit_constructor() must be implemented')

    def visit_func_decl(self, node):
        raise NotImplementedError('visit_func_decl() must be implemented')

    def visit_lambda(self, node):
        raise NotImplementedError('visit_lambda() must be implemented')

    def visit_func_ref(self, node):
        raise NotImplementedError('visit_func_ref() must be implemented')

    def visit_bottom_constant(self, node):
        raise NotImplementedError("visit_bottom_constant() must be implemented")

    def visit_null_constant(self, node):
        raise NotImplementedError("visit_null_constant() must be implemented")

    def visit_integer_constant(self, node):
        raise NotImplementedError(
            'visit_integer_constant() must be implemented')

    def visit_real_constant(self, node):
        raise NotImplementedError('visit_real_constant() must be implemented')

    def visit_char_constant(self, node):
        raise NotImplementedError('visit_char_constant() must be implemented')

    def visit_string_constant(self, node):
        raise NotImplementedError(
            'visit_string_constant() must be implemented')

    def visit_array_expr(self, node):
        raise NotImplementedError(
            'visit_array_expr() must be implemented')

    def visit_boolean_constant(self, node):
        raise NotImplementedError(
            'visit_boolean_constant() must be implemented')

    def visit_variable(self, node):
        raise NotImplementedError('visit_variable() must be implemented')

    def visit_unary_expr(self, node):
        raise NotImplementedError("visit_unary_expr() must be implemented")

    def visit_binary_expr(self, node):
        raise NotImplementedError("visit_binary_expr() must be implemented")

    def visit_logical_expr(self, node):
        raise NotImplementedError('visit_logical_expr() must be implemented')

    def visit_equality_expr(self, node):
        raise NotImplementedError('visit_equality_expr() must be implemented')

    def visit_comparison_expr(self, node):
        raise NotImplementedError(
            'visit_comparison_expr() must be implemented')

    def visit_arith_expr(self, node):
        raise NotImplementedError('visit_arith_expr() must be implemented')

    def visit_conditional(self, node):
        raise NotImplementedError('visit_conditional() must be implemented')

    def visit_multiconditional(self, node):
        raise NotImplementedError(
            "visit_multiconditional() must be implemented")

    def visit_is(self, node):
        raise NotImplementedError('visit_is() must be implemented')

    def visit_new(self, node):
        raise NotImplementedError('visit_new() must be implemented')

    def visit_field_access(self, node):
        raise NotImplementedError('visit_field_access() must be implemented')

    def visit_func_call(self, node):
        raise NotImplementedError('visit_func_call() must be implemented')

    def visit_assign(self, node):
        raise NotImplementedError('visit_assign() must be implemented')

    def visit_trycatch(self, node):
        raise NotImplementedError('visit_trycatch() must be implemented')

    def visit_return(self, node):
        raise NotImplementedError('visit_return() must be implemented')

    def visit_loop(self, node):
        raise NotImplementedError('visit_loop() must be implemented')


class DefaultVisitor(ASTVisitor):

    def result(self):
        raise NotImplementedError('result() must be implemented')

    def _visit_node(self, node):
        children = node.children()
        for c in children:
            c.accept(self)

    def visit_program(self, node):
        return self._visit_node(node)

    def visit_block(self, node):
        return self._visit_node(node)

    def visit_super_instantiation(self, node):
        return self._visit_node(node)

    def visit_class_decl(self, node):
        return self._visit_node(node)

    def visit_type_param(self, node):
        return self._visit_node(node)

    def visit_var_decl(self, node):
        return self._visit_node(node)

    def visit_call_argument(self, node):
        return self._visit_node(node)

    def visit_field_decl(self, node):
        return self._visit_node(node)

    def visit_param_decl(self, node):
        return self._visit_node(node)

    def visit_constructor(self, node):
        return self._visit_node(node)

    def visit_func_decl(self, node):
        return self._visit_node(node)

    def visit_lambda(self, node):
        return self._visit_node(node)

    def visit_func_ref(self, node):
        return self._visit_node(node)

    def visit_bottom_constant(self, node):
        return self._visit_node(node)

    def visit_null_constant(self, node):
        return self._visit_node(node)

    def visit_integer_constant(self, node):
        return self._visit_node(node)

    def visit_real_constant(self, node):
        return self._visit_node(node)

    def visit_char_constant(self, node):
        return self._visit_node(node)

    def visit_string_constant(self, node):
        return self._visit_node(node)

    def visit_array_expr(self, node):
        return self._visit_node(node)

    def visit_boolean_constant(self, node):
        return self._visit_node(node)

    def visit_variable(self, node):
        return self._visit_node(node)

    def visit_unary_expr(self, node):
        return self._visit_node(node)

    def visit_binary_expr(self, node):
        return self._visit_node(node)

    def visit_logical_expr(self, node):
        return self._visit_node(node)

    def visit_equality_expr(self, node):
        return self._visit_node(node)

    def visit_comparison_expr(self, node):
        return self._visit_node(node)

    def visit_arith_expr(self, node):
        return self._visit_node(node)

    def visit_conditional(self, node):
        return self._visit_node(node)

    def visit_multiconditional(self, node):
        return self._visit_node(node)

    def visit_is(self, node):
        return self._visit_node(node)

    def visit_new(self, node):
        return self._visit_node(node)

    def visit_field_access(self, node):
        return self._visit_node(node)

    def visit_func_call(self, node):
        return self._visit_node(node)

    def visit_assign(self, node):
        return self._visit_node(node)

    def visit_trycatch(self, node):
        return self._visit_node(node)

    def visit_return(self, node):
        return self._visit_node(node)

    def visit_loop(self, node):
        return self._visit_node(node)


class DefaultVisitorUpdate(DefaultVisitor):

    def result(self):
        raise NotImplementedError('result() must be implemented')

    def _visit_node(self, node):
        children = node.children()
        new_children = []
        for c in children:
            new_children.append(c.accept(self))
        node.update_children(new_children)
        return node


class ASTExprUpdate(DefaultVisitor):
    """
    This visitor class is used to update an expression node found
    in a parent node within a given AST.
    """
    def __init__(self, index: int, new_node: ast.Node):
        self.index = index
        self.new_node = new_node

    def visit_block(self, node):
        body = node.body
        body[self.index] = self.new_node

    def visit_var_decl(self, node):
        node.expr = self.new_node

    def visit_func_decl(self, node):
        node.body = self.new_node

    def visit_lambda(self, node):
        if isinstance(node.body, ast.Expr):
            node.body = self.new_node

    def visit_func_ref(self, node):
        node.receiver = self.new_node

    def visit_array_expr(self, node):
        node.exprs[self.index] = self.new_node

    def visit_unary_expr(self, node):
        node.expr = self.new_node

    def visit_binary_expr(self, node):
        if self.index == 0:
            node.lexpr = self.new_node
        else:
            node.rexpr = self.new_node

    def visit_logical_expr(self, node):
        self.visit_binary_expr(node)

    def visit_equality_expr(self, node):
        self.visit_binary_expr(node)

    def visit_comparison_expr(self, node):
        self.visit_binary_expr(node)

    def visit_arith_expr(self, node):
        self.visit_binary_expr(node)

    def visit_conditional(self, node):
        if self.index == 0:
            node.cond = self.new_node
        elif self.index == 1:
            node.true_branch = self.new_node
        else:
            node.false_branch = self.new_node

    def visit_multiconditional(self, node):
        i = 0 if not node.root_cond else 1
        if node.root_cond and self.index == 0:
            node.root_cond = self.new_node
        if self.index >= len(node.conditions) + i:
            index = self.index - len(node.conditions) - i
            node.branches[index] = self.new_node
        else:
            node.conditions[self.index] = self.new_node

    def visit_trycatch(self, node):
        if self.index == 0:
            node.try_block = self.new_node
        catch_blocks = OrderedDict()
        for i, (k, v) in enumerate(node.catch_blocks.items()):
            if i + 1 == self.index:
                catch_blocks[k] = self.new_node
            else:
                catch_blocks[k] = v
        node.catch_blocks = catch_blocks

    def visit_new(self, node):
        node.args[self.index] = ast.CallArgument(self.new_node)

    def visit_field_access(self, node):
        node.receiver = self.new_node

    def visit_func_call(self, node):
        assert self.index in range(-1, len(node.args))
        if self.index == -1:
            node.receiver = self.new_node
        else:
            node.args[self.index] = ast.CallArgument(self.new_node)

    def visit_assign(self, node):
        assert self.index in [-1, 0]
        if self.index == -1:
            node.receiver = self.new_node
        else:
            node.expr = self.new_node

    def visit_return(self, node):
        if self.index == 0:
            node.expr = self.new_node

    def visit_loop(self, node):
        if self.index == 0:
            node.block = self.new_node
        else:
            node.cond = self.new_node
