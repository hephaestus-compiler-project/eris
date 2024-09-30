import re

from src.ir import ast, scala_types as sc, types as tp
from src.transformations.base import change_namespace
from src.translators.base import BaseTranslator
from src.translators.utils import (
    strip_fqn, get_modifier_list, is_parent_interface,
    get_class_type_from_context, package_consistency)


def append_to(visit):
    def inner(self, node):
        self._nodes_stack.append(node)
        visit(self, node)
        self._nodes_stack.pop()
    return inner


class ScalaTranslator(BaseTranslator):

    filename = "program.scala"
    incorrect_filename = "incorrect.scala"
    ident_value = " "

    EXCLUDED_METADATA = ["final", "override", "open", "static", "default",
                         "public"]

    def __init__(self, package=None, options={}):
        super().__init__(package, options)
        self._children_res = []
        self.ident = 0
        self.is_unit = False
        self.is_lambda = False
        self._cast_integers = False
        self.context = None
        self._namespace = ast.GLOBAL_NAMESPACE

        # We need nodes_stack to assign lambdas to vars when needed.
        # Specifically, in visit_lambda we use `var y = ` as a prefix only if
        # parent node is a block and its parent is a function declaration that
        # return Unit.
        self._nodes_stack = [None]

    def _reset_state(self):
        self._children_res = []
        self.ident = 0
        self.is_unit = False
        self.is_lambda = False
        self._cast_integers = False
        self._nodes_stack = [None]
        self.context = None
        self._namespace = ast.GLOBAL_NAMESPACE

    def _package_consistency(self, res: str) -> str:
        if self.package:
            package_prefix = self.package.split(".", 1)[0]
            new = re.sub(package_prefix + r"\.[a-z]+", self.package, res)
            return new
        return res

    def get_ident(self, extra=0, old_ident=None):
        if old_ident:
            return old_ident * self.ident_value
        return (self.ident + extra) * self.ident_value

    @staticmethod
    def get_filename():
        return ScalaTranslator.filename

    @staticmethod
    def get_incorrect_filename():
        return ScalaTranslator.incorrect_filename

    def type_arg2str(self, t_arg):
        if not isinstance(t_arg, tp.WildCardType):
            return self.get_type_name(t_arg)
        if t_arg.is_invariant():
            return "?"
        elif t_arg.is_covariant():
            return "? <: " + self.get_type_name(t_arg.bound)
        else:
            return "? >: " + self.get_type_name(t_arg.bound)

    @package_consistency
    @strip_fqn
    def get_type_name(self, t):
        if t.is_wildcard():
            t = t.get_bound_rec()
            if t is None:
                t = sc.Any
            return self.get_type_name(t)
        if isinstance(t, sc.RawType):
            converted_t = t.t_constructor.new(
                [tp.WildCardType()
                 for _ in range(len(t.t_constructor.type_parameters))])
            return self.get_type_name(converted_t)
        t_constructor = getattr(t, 't_constructor', None)
        if not t_constructor:
            return t.get_name()
        if isinstance(t_constructor, tp.NullableType):
            return "{t} | Null".format(t=self.get_type_name(t.type_args[0]))
        return "{}[{}]".format(t.name, ", ".join([self.type_arg2str(ta)
                                                  for ta in t.type_args]))

    def pop_children_res(self, children):
        len_c = len(children)
        if not len_c:
            return []
        res = self._children_res[-len_c:]
        self._children_res = self._children_res[:-len_c]
        return res

    def visit_program(self, node):
        self.context = node.context
        self.lib_spec = node.lib
        children = node.children()
        for c in children:
            c.accept(self)
        if self.package:
            package_str = 'package ' + self.package + '\n'
        else:
            package_str = ''
        bottom = "def bottom[T](): T = ???\n\n"
        self.program = package_str + bottom + '\n\n'.join(
            self.pop_children_res(children))

    @append_to
    def visit_block(self, node):
        children = node.children()
        is_unit = self.is_unit
        is_lambda = self.is_lambda
        self.is_unit = False
        self.is_lambda = False
        for c in children:
            c.accept(self)
        children_res = self.pop_children_res(children)
        res = (self.get_ident(old_ident=max(self.ident - 2, 0)) + "{"
               if not is_lambda else "")
        res += "\n" + "\n".join(children_res[:-1])
        if children_res[:-1]:
            res += "\n"
        body = ""
        if children_res:
            body = children_res[-1]
        if children_res:
            res += body + "\n"
        else:
            res += "\n"
        res += (self.get_ident(old_ident=max(self.ident - 2, 0)) + "}"
                if not is_lambda else "")
        self.is_unit = is_unit
        self.is_lambda = is_lambda
        self._children_res.append(res)

    @append_to
    def visit_super_instantiation(self, node):
        self.ident = 0
        children = node.children()
        for c in children:
            c.accept(self)
        children_res = self.pop_children_res(children)
        if node.args is None:
            self._children_res.append(self.get_type_name(node.class_type))
            return
        self._children_res.append(
            self.get_type_name(node.class_type) + "(" + ", ".join(
                children_res) + ")")

    def create_companion_object(self, node):
        static_fields = [f for f in node.fields
                         if f.metadata.get("static", False)]
        static_methods = [m for m in node.functions
                          if m.metadata.get("static", False)]
        static_inner_members = [
            c for c in node.extra_declarations
            if c.metadata.get("static", False)
        ]
        children = static_inner_members + static_fields + static_methods
        if not children:
            return None
        old_ident = self.ident
        for c in children:
            c.accept(self)
        children_res = self.pop_children_res(children)
        base_cls_name = node.name.rsplit(".", 1)[-1]
        res = "{ident}object {name} {{\n{body}\n{ident}}}".format(
            ident=self.get_ident(old_ident=old_ident),
            name=base_cls_name,
            body="\n\n".join(children_res)
        )
        self.ident = old_ident
        return res

    def _create_children(self, children):
        for c in children:
            c.accept(self)
        return self.pop_children_res(children)

    def create_fields(self, node):
        non_static_fields = [f for f in node.fields
                             if not f.metadata.get("static", False)]
        return self._create_children(non_static_fields)

    def create_functions(self, node):
        non_static_functions = [m for m in node.functions
                                if not m.metadata.get("static", False)]
        return self._create_children(non_static_functions)

    def create_constructors(self, node):
        if not node.constructors:
            return self._create_children(node.constructors)
        if len(node.constructors) == 1:
            primary_con = node.constructors[0]
        else:
            primary_constructors = [
                con
                for con in node.constructors
                if con.metadata.get("primary", False)
            ]
            primary_con = primary_constructors[0]
        res = ",".join([
            f"{p.name}: {self.get_type_name(p.get_type())}"
            for p in primary_con.params
        ])
        res = f"({res})"
        children_res = [res]
        secondary_cons = [con for con in node.constructors
                          if con != primary_con]
        children_res.extend(self._create_children(secondary_cons))
        return children_res

    def create_type_params(self, node):
        return self._create_children(node.type_parameters)

    def create_extra_declarations(self, node):
        non_static_members = [c for c in node.extra_declarations
                              if not c.metadata.get("static", False)]
        return self._create_children(non_static_members)

    def get_superclasses_interfaces(self, node: ast.ClassDeclaration):
        superclasses, interfaces = [], []
        for cls_inst in node.superclasses:
            cls_name = cls_inst.class_type.name
            if cls_name in [sc.Any.name, sc.AnyRef.name]:
                continue
            cls_inst = self.get_type_name(cls_inst.class_type)
            class_type = get_class_type_from_context(
                cls_name, self.context, self._namespace, self.lib_spec)
            is_interface = (class_type == ast.ClassDeclaration.INTERFACE or
                            is_parent_interface(node.name, cls_name,
                                                self.lib_spec))
            if is_interface:
                interfaces.append(cls_inst)
                continue
            super_cls_name = cls_inst
            if not node.constructors:
                super_cls_name += "()"
                superclasses.append(super_cls_name)
                continue

            if len(node.constructors) == 1:
                primary_con = node.constructors[0]
            else:
                # Find the primary constructor
                primary_cons = [
                    con for con in node.constructors
                    if con.metadata.get("primary", False)
                ]
                primary_con = primary_cons[0]

            # Find the super call (if any) inside the primary constructor
            super_call = None
            for elem in primary_con.body.body:
                if isinstance(elem, ast.FunctionCall) and \
                        elem.func == ast.FunctionCall.SUPER:
                    super_call = elem
                    break
            res = ""
            if super_call:
                res = self._create_children([elem])[0].replace(
                    ast.FunctionCall.SUPER, "").replace("`", "").lstrip()
            superclasses.append(super_cls_name + res)
        return superclasses + interfaces

    @append_to
    @change_namespace
    def visit_class_decl(self, node):

        old_ident = self.ident
        self.ident += 2
        companion_obj = self.create_companion_object(node)
        field_res = self.create_fields(node)
        function_res = self.create_functions(node)
        constr_res = self.create_constructors(node)
        type_parameters_res = self.create_type_params(node)
        extra_decl_res = self.create_extra_declarations(node)

        class_prefix = node.get_class_prefix().replace("interface", "trait")
        base_cls_name = node.name.rsplit(".", 1)[-1]
        superclasses = self.get_superclasses_interfaces(node)
        use_final = (
            node.is_final and node.class_type != ast.ClassDeclaration.INTERFACE
        )
        res = "{ident}{o}{p} {n}".format(
            ident=" " * old_ident,
            o="final " if use_final else "",
            p=class_prefix,
            n=base_cls_name
        )
        start_brace = (field_res or constr_res or function_res or
                       extra_decl_res)
        primary_con_res = ""
        if constr_res:
            primary_con_res = constr_res[0]
            constr_res = constr_res[1:]

        if type_parameters_res:
            res = "{}[{}]".format(res, ", ".join(type_parameters_res))

        if primary_con_res:
            res += primary_con_res

        if superclasses:
            res += " extends " + ", ".join(superclasses)

        # Now create the body of class consisting of field, functions,
        # constructors, or other nested classes.

        # Now add a starting curly brace.
        if start_brace:
            res += " " * old_ident + "{\n"
        if field_res:
            res += "\n".join(field_res) + "\n"
        if constr_res:
            res += "\n\n".join(constr_res) + "\n"
        if function_res:
            res += "\n\n".join(function_res) + "\n"
        if extra_decl_res:
            res += "\n\n".join(extra_decl_res) + "\n"
        # Add an ending curly brace.
        if start_brace:
            res += " " * old_ident + "}"

        if companion_obj:
            res += "\n"
            res += companion_obj.lstrip()

        self.ident = old_ident
        self._children_res.append(res)

    @append_to
    def visit_type_param(self, node):
        self._children_res.append("{variance}{name}{bound}".format(
            variance=(
                ("+" if node.is_covariant() else "-")
                if not node.is_invariant()
                else ""
            ),
            name=self.get_type_name(node),
            bound=(
                "<: " + self.get_type_name(node.bound)
                if node.bound is not None
                else ""
            )
        ))

    @append_to
    def visit_var_decl(self, node):
        old_ident = self.ident
        prefix = " " * self.ident
        self.ident = 0
        children = node.children()
        prev = self._cast_integers
        if node.var_type is None:
            self._cast_integers = True
        for c in children:
            c.accept(self)
        children_res = self.pop_children_res(children)
        var_type = "val " if node.is_final else "var "
        res = prefix + var_type + node.name
        if node.var_type is not None:
            res += ": " + self.get_type_name(node.var_type)
        res += " = " + children_res[0]
        self.ident = old_ident
        self._cast_integers = prev
        self._children_res.append(res)

    @append_to
    def visit_call_argument(self, node):
        old_ident = self.ident
        self.ident = 0
        children = node.children()
        for c in node.children():
            c.accept(self)
        self.ident = old_ident
        children_res = self.pop_children_res(children)
        res = children_res[0]
        if node.name:
            res = node.name + " = " + res
        self._children_res.append(res)

    @append_to
    def visit_field_decl(self, node):
        prefix = " " * self.ident
        prefix += '' if node.can_override else 'final '
        prefix += '' if not node.override else 'override '
        modifiers = get_modifier_list({k: v for k, v in node.metadata.items()
                                       if (k not in self.EXCLUDED_METADATA and
                                           v not in self.EXCLUDED_METADATA)})
        prefix += " ".join(modifiers) + " " if modifiers else ""
        prefix += 'val ' if node.is_final else 'var '
        res = prefix + node.name + ": " + self.get_type_name(node.field_type)
        if "abstract" not in modifiers:
            res += " = ???"
        self._children_res.append(res)

    @append_to
    def visit_param_decl(self, node):
        old_ident = self.ident
        self.ident = 0
        children = node.children()
        for c in node.children():
            c.accept(self)
        self.ident = old_ident
        vararg_str = '*' if node.vararg else ''
        # Recall that varargs ara actually arrays in the signature of
        # the corresponding parameters.
        param_type = (
            node.param_type.type_args[0]
            if node.vararg and node.param_type.name == sc.Array.name
            else node.param_type
        )
        res = node.name + ": " + self.get_type_name(param_type) + vararg_str
        if len(children):
            children_res = self.pop_children_res(children)
            res += " = " + children_res[0]
        self._children_res.append(res)

    @append_to
    @change_namespace
    def visit_constructor(self, node):
        old_ident = self.ident
        self.ident += 2
        children = node.children()
        for c in children:
            c.accept(self)
        children_res = self.pop_children_res(children)
        param_res = [children_res[i] for i, _ in enumerate(node.params)]
        body_res = children_res[-1] if node.body else ''
        res = "{ident}def this({params}) = {body}".format(
            ident=" " * old_ident,
            params=", ".join(param_res),
            body=body_res
        )
        self.ident = old_ident
        self._children_res.append(res)

    @append_to
    @change_namespace
    def visit_func_decl(self, node):
        old_ident = self.ident
        self.ident += 2
        children = node.children()
        prev_is_unit = self.is_unit
        self.is_unit = node.get_type() == sc.Unit
        prev_c = self._cast_integers
        is_expression = not isinstance(node.body, ast.Block)
        if is_expression:
            self._cast_integers = True
        for c in children:
            c.accept(self)
        children_res = self.pop_children_res(children)
        param_res = [children_res[i] for i, _ in enumerate(node.params)]
        len_params = len(node.params)
        len_type_params = len(node.type_parameters)
        type_parameters_res = ", ".join(
            children_res[len_params:len_type_params + len_params])
        body_res = children_res[-1] if node.body else ''
        prefix = " " * old_ident
        prefix += (
            "final "
            if (node.is_final and
                node.func_type == ast.FunctionDeclaration.CLASS_METHOD)
            else ""
        )
        prefix += "" if not node.override else "override "
        modifiers = get_modifier_list({k: v for k, v in node.metadata.items()
                                       if (k not in self.EXCLUDED_METADATA
                                           and v not in self.EXCLUDED_METADATA)
                                       })
        prefix += " ".join(modifiers) + " " if modifiers else ""
        type_params = (
            "[" + type_parameters_res + "]" if type_parameters_res else "")
        res = prefix + "def " + f"`{node.name}`" + type_params + "(" + ", ".join(
            param_res) + ")"
        if node.ret_type:
            res += ": " + self.get_type_name(node.ret_type)
        if body_res:
            sign = "="
            res += " " + sign + "\n" + body_res
        self.ident = old_ident
        self.is_unit = prev_is_unit
        self._cast_integers = prev_c
        self._children_res.append(res)

    @append_to
    def visit_lambda(self, node):

        old_ident = self.ident
        is_expression = not isinstance(node.body, ast.Block)
        self.ident = 0 if is_expression else self.ident + 2
        children = node.children()

        prev_is_unit = self.is_unit
        prev_is_lambda = self.is_lambda
        self.is_unit = node.get_type() == sc.Unit
        self.is_lambda = True

        prev_c = self._cast_integers
        if is_expression:
            self._cast_integers = True

        for c in children:
            c.accept(self)
        children_res = self.pop_children_res(children)
        self.ident = old_ident

        param_res = [children_res[i] for i, _ in enumerate(node.params)]
        body_res = children_res[-1] if node.body else ''
        ret_type_str = (
            ": " + self.get_type_name(node.ret_type)
            if node.ret_type
            else ""
        )
        if node.can_infer_signature:
            param_res = [p.name for p in node.params]
            ret_type_str = ""

        # use the lambda syntax: { params -> stmt }
        res = "({params}) => {body}{ret}".format(
            params=", ".join(param_res),
            body=body_res,
            ret=ret_type_str
        )
        self.is_unit = prev_is_unit
        self.is_lambda = prev_is_lambda
        self._cast_integers = prev_c
        self._children_res.append(res)

    @append_to
    def visit_bottom_constant(self, node):
        bottom = (
            "bottom()"
            if not node.t
            else "bottom[{}]()".format(self.get_type_name(node.t))
        )
        self._children_res.append((self.ident * " ") + bottom)

    @append_to
    def visit_null_constant(self, node):
        res = "{ident}null.asInstanceOf[{t} | Null]".format(
            ident=self.get_ident(),
            t=f"({self.get_type_name(node.t)}) " if node.t else ""
        )
        self._children_res.append(res)

    @append_to
    def visit_integer_constant(self, node):
        if not self._cast_integers:
            self._children_res.append(" " * self.ident + str(node.literal))
            return
        integer_types = {
            sc.Long: ".toLong",
            sc.Short: ".toShort",
            sc.Byte: ".toByte",
            sc.Number: ".asInstanceOf[Number]",
        }
        suffix = integer_types.get(node.integer_type, "")
        literal = str(node.literal)
        self._children_res.append(" " * self.ident + literal + suffix)

    @append_to
    def visit_real_constant(self, node):
        real_types = {
            sc.Float: "f"
        }
        suffix = real_types.get(node.real_type, "")
        self._children_res.append(
            " " * self.ident + str(node.literal) + suffix)

    @append_to
    def visit_char_constant(self, node):
        self._children_res.append("{}'{}'".format(
            " " * self.ident, node.literal))

    @append_to
    def visit_string_constant(self, node):
        self._children_res.append('{}"{}"'.format(
            " " * self.ident, node.literal))

    @append_to
    def visit_boolean_constant(self, node):
        self._children_res.append(" " * self.ident + str(node.literal))

    @append_to
    def visit_array_expr(self, node):
        if not node.length:
            if node.array_type.type_args[0].has_type_variables():
                self._children_res.append(
                    "{}Array[Any]().asInstanceOf[Array[{}]]".format(
                        " " * self.ident,
                        self.get_type_name(node.array_type.type_args[0])
                    )
                )
            else:
                self._children_res.append(
                    "{}Array[{}]()".format(
                        " " * self.ident,
                        self.get_type_name(node.array_type.type_args[0])
                    )
                )
            return
        old_ident = self.ident
        self.ident = 0
        children = node.children()
        for c in children:
            c.accept(self)
        children_res = self.pop_children_res(children)
        self.ident = old_ident

        template = "{ident}Array[{type_arg}]({values})"
        t_arg = self.get_type_name(node.array_type.type_args[0])
        if node.array_type.type_args[0].is_type_var():
            t_arg = "Any"
        array_expr = template.format(ident=" " * self.ident,
                                     type_arg=t_arg,
                                     values=", ".join(children_res))
        if node.array_type.type_args[0].is_type_var():
            array_expr += ".asInstanceOf[Array[{}]]".format(
                self.get_type_name(node.array_type.type_args[0]))
        self._children_res.append(array_expr)

    @append_to
    def visit_variable(self, node):
        self._children_res.append(" " * self.ident + node.name)

    @append_to
    def visit_unary_expr(self, node):
        old_ident = self.ident
        self.ident = 0
        children = node.children()
        for c in children:
            c.accept(self)
        children_res = self.pop_children_res(children)
        if node.is_prefix:
            res = "{ident}{operator}({expr})"
        else:
            res = "{ident}({expr}){operator}"
        res = res.format(
            ident=self.get_ident(old_ident=old_ident),
            operator=node.operator,
            expr=children_res[0]
        )
        self.ident = old_ident
        self._children_res.append(res)

    @append_to
    def visit_binary_expr(self, node):
        old_ident = self.ident
        self.ident = 0
        children = node.children()
        for c in children:
            c.accept(self)
        children_res = self.pop_children_res(children)
        res = "{ident}({left} {op} {right})".format(
            ident=" " * old_ident,
            left=(
                children_res[0]
                if not isinstance(node.lexpr,
                                  (ast.FunctionReference, ast.Lambda))
                else "({})".format(children_res[0])
            ),
            op=node.operator,
            right=(
                children_res[1]
                if not isinstance(node.rexpr,
                                  (ast.FunctionReference, ast.Lambda))
                else "({})".format(children_res[1])
            )
        )
        self.ident = old_ident
        self._children_res.append(res)

    def visit_logical_expr(self, node):
        self.visit_binary_expr(node)

    def visit_equality_expr(self, node):
        prev = self._cast_integers
        # When we encounter equality epxressions,
        # we need to explicitly cast integer literals.
        # Kotlin does not permit operations like the following
        # val d: Short = 1
        # d == 2
        #
        # As a workaround, we can do
        # d == 2.toShort()
        # XXX
        # self._cast_integers = True
        self.visit_binary_expr(node)
        self._cast_integers = prev

    def visit_comparison_expr(self, node):
        self.visit_binary_expr(node)

    def visit_arith_expr(self, node):
        self.visit_binary_expr(node)

    @append_to
    def visit_conditional(self, node):
        prev_namespace = self._namespace
        children = node.children()
        children[0].accept(self)  # cond
        old_ident = self.ident
        self.ident += 2
        self._namespace = prev_namespace + ('true_block',)
        children[1].accept(self)  # true branch
        self._namespace = prev_namespace + ('false_block',)
        children[2].accept(self)  # false branch
        self._namespace = prev_namespace
        children_res = self.pop_children_res(children)
        if node.is_expression:
            res = "{ident}(if ({cond}) then\n{body}\n{ident}else\n{else_body})".format(
                ident=self.get_ident(old_ident=old_ident),
                cond=children_res[0].lstrip(),
                body=children_res[1],
                else_body=children_res[2]
            )
        else:
            res = "{ident}if (({if_condition})) then\n{body}\n{ident}else\n{else_body}".format(
                ident=self.get_ident(old_ident=old_ident),
                if_condition=children_res[0].lstrip(),
                body=children_res[1],
                else_body=children_res[2]
            )
        self.ident = old_ident
        self._children_res.append(res)

    @append_to
    def visit_multiconditional(self, node):
        prev_namespace = self._namespace
        children = node.children()
        i = 0
        if node.root_cond is not None:
            children[i].accept(self)  # cond
            i += 1

        old_ident = self.ident
        self.ident += 2
        for j in range(len(node.conditions)):
            children[i + j].accept(self)  # conditions
        i = i + len(node.conditions)
        for j in range(len(node.branches)):
            self._namespace = prev_namespace + (f'case_block{j}',)
            children[i + j].accept(self)  # branches

        self._namespace = prev_namespace
        children_res = self.pop_children_res(children)
        root_cond_res = None
        i = 0
        if node.root_cond is not None:
            root_cond_res = children_res[0]
            i += 1
        condition_res = children_res[i:len(node.conditions) + i]
        i = i + len(node.conditions)
        branch_res = children_res[i:]
        assert len(condition_res) == len(branch_res) or \
            len(condition_res) == len(branch_res) - 1

        open_paren, close_paren = ("(", ")") if node.is_expression else ("",
                                                                         "")
        prefix = (
            "match {\n"
            if node.root_cond is None
            else "({expr}) match {{\n".format(expr=root_cond_res.lstrip())
        )
        case_exprs_str = []
        for i in range(len(node.branches)):
            if i < len(node.conditions):
                case_exprs_str.append(
                    "{ident}case {case_expr} => {body}".format(
                        ident=self.get_ident(),
                        case_expr=condition_res[i].lstrip().strip(),
                        body=branch_res[i].lstrip()
                    )
                )
            else:
                case_exprs_str.append(
                    "{ident}case _ => {body}".format(
                        ident=self.get_ident(),
                        body=branch_res[i].lstrip()
                    )
                )

        res = "{ident}{op}{prefix}{body}\n{ident}".format(
            ident=self.get_ident(old_ident=old_ident),
            op=open_paren,
            prefix=prefix,
            body="\n".join(case_exprs_str)
        )
        res += "}" + close_paren
        self.ident = old_ident
        self._children_res.append(res)

    @append_to
    def visit_is(self, node):
        old_ident = self.ident
        self.ident = 0
        children = node.children()
        for c in children:
            c.accept(self)
        children_res = self.pop_children_res(children)
        res = "{ident}{expr}.isInstanceOf[{t}]".format(
            ident=self.get_ident(old_ident=old_ident),
            expr=children_res[0],
            t=self.get_type_name(node.etype))
        self.ident = old_ident
        self._children_res.append(res)

    @append_to
    def visit_new(self, node):
        old_ident = self.ident
        self.ident = 0
        children = node.children()
        for c in children:
            c.accept(self)
        children_res = self.pop_children_res(children)
        self.ident = old_ident
        if node.class_type == sc.Any:
            self._children_res.append("{ident}1.asInstanceOf[Any]".format(
                ident=" " * self.ident))
        # Remove type arguments from Parameterized Type
        elif getattr(node.class_type, 'can_infer_type_args', None) is True:
            self._children_res.append("new {prefix}({values})".format(
                prefix=" " * self.ident + node.class_type.name,
                values=", ".join(children_res)))
        else:
            self._children_res.append("new {prefix}({values})".format(
                prefix=" " * self.ident + self.get_type_name(node.class_type),
                values=", ".join(children_res)))

    @append_to
    def visit_field_access(self, node):
        old_ident = self.ident
        self.ident = 0
        children = node.children()
        for c in children:
            c.accept(self)
        children_res = self.pop_children_res(children)
        self.ident = old_ident
        if children:
            receiver_expr = (
                '({}).'.format(children_res[0])
                if isinstance(node.expr, ast.BottomConstant)
                else children_res[0] + "."
            )
        else:
            receiver_expr = ""
        res = "{}{}{}".format(" " * self.ident, receiver_expr, node.field)
        res = self._package_consistency(res)
        self._children_res.append(res)

    @append_to
    def visit_func_ref(self, node):
        def inside_block_unit_function():
            if (isinstance(self._nodes_stack[-2], ast.Block) and
                    isinstance(self._nodes_stack[-3], (ast.Lambda,
                               ast.FunctionDeclaration)) and
                    self._nodes_stack[-3].ret_type == sc.Unit):
                return True
            return False

        old_ident = self.ident

        self.ident = 0
        children = node.children()
        for c in children:
            c.accept(self)

        self.ident = old_ident

        children_res = self.pop_children_res(children)
        receiver = children_res[0] + "." if children_res else ""
        if node.func == ast.FunctionReference.NEW_REF:
            param_len = len(node.receiver.args)
            param_str = ", ".join("_" for _ in range(param_len))
            param_str = f"({param_str})"
            res = "{ident}{assign}{params} => {receiver}".format(
                ident=" " * self.ident,
                assign="" if not inside_block_unit_function() else "val _y = ",
                params=param_str,
                receiver=receiver[:-1],
            )
        elif len(node.function_type.type_args) != 1:
            # We reference a method that takes at least one parameter
            res = "{ident}{assign}{receiver}{name}".format(
                ident=" " * self.ident,
                assign="" if not inside_block_unit_function() else "val _y = ",
                receiver=receiver,
                name=node.func
            )
        else:
            # We reference a method that takes no parameters
            res = "{ident}{assign}() => {receiver}{name}()".format(
                ident=" " * self.ident,
                assign="" if not inside_block_unit_function() else "val _y = ",
                receiver=receiver,
                name=node.func
            )
        res = self._package_consistency(res)
        self._children_res.append(res)

    @append_to
    def visit_func_call(self, node):
        old_ident = self.ident
        self.ident = 0
        children = node.children()
        for c in children:
            c.accept(self)
        self.ident = old_ident
        children_res = self.pop_children_res(children)
        type_args = (
            "[" + ",".join(
                [self.get_type_name(t) for t in node.type_args]) + "]"
            if not node.can_infer_type_args and node.type_args
            else ""
        )
        segs = node.func.rsplit(".", 1)
        if node.receiver:
            receiver_expr = (
                '({})'.format(children_res[0])
                if isinstance(node.receiver, ast.BottomConstant)
                else children_res[0]
            )
            args = children_res[1:]
            func = node.func
        else:
            receiver_expr, func = (
                ("", node.func)
                if len(segs) == 1
                else (segs[0], segs[1])
            )
            args = children_res
        if receiver_expr:
            receiver_expr += "."
        if func not in [ast.FunctionCall.SUPER, ast.FunctionCall.THIS]:
            func = f"`{func}`"
        res = "{ident}{rec}{func}{type_args}({args})".format(
            ident=" " * self.ident,
            rec=receiver_expr,
            func=func,
            type_args=type_args,
            args=", ".join(args)
        )
        res = self._package_consistency(res)
        self._children_res.append(res)

    @append_to
    def visit_assign(self, node):
        old_ident = self.ident
        prev = self._cast_integers
        self._cast_integers = True
        self.ident = 0
        children = node.children()
        for c in children:
            c.accept(self)
        self.ident = old_ident
        children_res = self.pop_children_res(children)
        if node.receiver:
            receiver_expr = (
                '({})'.format(children_res[0])
                if isinstance(node.receiver, ast.BottomConstant)
                else children_res[0]
            )
            res = "{ident}{rec}.{field} = {expr}".format(
                ident=" " * old_ident,
                rec=receiver_expr,
                field=node.name,
                expr=children_res[1]
            )
        else:
            res = "{ident}{field} = {expr}".format(
                ident=" " * old_ident,
                field=node.name,
                expr=children_res[0]
            )
        res = self._package_consistency(res)
        self.ident = old_ident
        self._cast_integers = prev
        self._children_res.append(res)

    @append_to
    def visit_trycatch(self, node):
        prev_namespace = self._namespace
        children = node.children()
        old_ident = self.ident
        self.ident += 2
        children[0].accept(self)  # try
        self._namespace = prev_namespace + ('try_block',)
        for i, k in enumerate(node.catch_blocks):
            self._namespace = prev_namespace + (f"catch_{k}_block",)
            children[i + 1].accept(self)
        children_res = self.pop_children_res(children)
        ident = self.get_ident(old_ident=old_ident)
        catch_bodies = [
            f"{ident}case (e: {k}) => \n{children_res[i + 1]}"
            for i, k in enumerate(node.catch_blocks.keys())
        ]
        catch_bodies_str = "\n".join(catch_bodies)
        catch_bodies_str = f"{ident}catch {{\n{catch_bodies_str}\n{ident}}}"

        res = f"{ident}(try\n{children_res[0]}\n{catch_bodies_str})"
        self.ident = old_ident
        self._namespace = prev_namespace
        self._children_res.append(res)
        return res

    @append_to
    def visit_return(self, node):
        children = node.children()
        for c in children:
            c.accept(self)
        children_res = self.pop_children_res(children)
        res = "{ident}return {expr}".format(
            ident=self.get_ident(old_ident=self.ident),
            expr=children_res[0].lstrip() if node.expr else ""
        )
        self._children_res.append(res)
        return res

    @append_to
    def visit_loop(self, node):
        children = node.children()
        for c in children:
            c.accept(self)
        children_res = self.pop_children_res(children)
        loop_prefix = (
            "while (bottom[Boolean]())"
            if node.loop_type == ast.Loop.WHILE_LOOP
            else "for (n <- Seq())"
        )
        res = "{ident}{prefix}{body}".format(
            ident=self.get_ident(old_ident=self.ident),
            prefix=loop_prefix,
            body=children_res[0]
        )
        self._children_res.append(res)
        return res
