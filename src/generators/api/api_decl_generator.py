from collections import namedtuple
from copy import deepcopy
import json
import functools
import re
from typing import List, Tuple, Union

from src import utils
from src.enumerators import get_error_enumerator
from src.ir import ast, types as tp
from src.ir.context import Context
from src.compilers import compile_program
from src.generators.api import builder, api_graph as ag, matcher as match
from src.generators.api.api_generator import APIClientGenerator
from src.generators.api.special_methods import GROOVY_SPECIAL_METHODS
from src.modules.logging import log, log_onerror, log_error


def get_extra_api_components(spec: dict, predicate):
    sub_spec = {}
    for k, v in spec.items():
        if predicate(v):
            sub_spec[k] = v
    return sub_spec


def get_base_api_name(fqn: str, spec: dict):
    segs = fqn.rsplit(".", 1)
    assert len(segs) == 2
    parent, base = tuple(segs)
    if parent not in spec or not spec[parent].get("is_class", True):
        # We encounter this case: mypkg.MyCls
        return base

    # Perhaps, we encounter a nested class/component.
    return get_base_api_name(parent, spec) + "." + base


def get_namespace_from_name(fqn: str, spec: dict):
    segs = fqn.rsplit(".", 1)
    assert len(segs) == 2
    parent, base = tuple(segs)
    if parent not in spec or not spec[parent].get("is_class", True):
        # We encounter this case: mypkg.MyCls
        return ast.GLOBAL_NAMESPACE
    return get_namespace_from_name(parent, spec) + (parent,)


def get_parent_classes(fqn: str, spec: dict) -> List[str]:
    segs = fqn.rsplit(".", 1)
    assert len(segs) == 2
    parent, base = tuple(segs)
    if parent not in spec or not spec[parent].get("is_class", True):
        # We encounter this case: mypkg.MyCls
        return []
    return get_parent_classes(parent, spec) + [parent]


def get_nested_classes(fqn: str, spec: dict) -> List[str]:
    return [
        k for k, v in spec.items()
        if k != fqn and k.startswith(fqn + ".") and v.get("is_class", True)
    ]


def to_namespace(m: ag.Method) -> str:
    param_str = ", ".join([str(p.t) for p in m.parameters])
    func_name = m.name
    if "." in func_name:
        func_name = func_name.rsplit(".", 1)[1]
    return f"{func_name}({param_str})"


def is_parent_interface(child_name: str, parent_name: str,
                        api_spec: dict) -> bool:
    class_type = api_spec.get(parent_name, {}).get("class_type")
    if class_type is not None:
        return class_type

    assert child_name in api_spec, "Child class specification not found"

    cls_spec = api_spec[child_name]
    return not any(sc.startswith(parent_name)
                   for sc in cls_spec["inherits"])


def is_definition_abstract(ns_spec: dict, node: Union[ag.Method, ag.Field]):
    is_parent_abstract = (
        ns_spec.get("class_type") == ast.ClassDeclaration.INTERFACE)
    is_abstract = (not node.metadata.get("static", False) and
                   not node.metadata.get("default", False) and
                   (is_parent_abstract or
                    node.metadata.get("abstract", False)))
    return is_abstract


Namespace = namedtuple("namespace", ["api_component_name"])


class APIDeclarationGenerator(APIClientGenerator):
    API_GRAPH_BUILDERS = {
        "java": builder.JavaAPIGraphBuilder,
        "kotlin": builder.KotlinAPIGraphBuilder,
        "groovy": builder.JavaAPIGraphBuilder,
        "scala": builder.ScalaAPIGraphBuilder,
    }

    def __init__(self, api_docs, options={}, language=None,
                 logger=None):
        super().__init__(api_docs, options=options, language=language,
                         logger=logger)
        self.options = options
        api_docs.update(GROOVY_SPECIAL_METHODS)
        self.api_docs = api_docs
        self.initial_api_graph = self.api_graph
        self.package_name = None
        api_rules_file = options.get("api-rules")
        api_namespaces = list(api_docs.keys())
        if api_rules_file:
            matcher = match.parse_rule_file(api_rules_file)
            api_namespaces = [k for k in api_namespaces
                              if matcher.match(Namespace(k))]
        self.api_namespaces = utils.random.shuffle(api_namespaces)
        self.programs_gen = self.compute_programs()
        self.ErrorEnumerator = get_error_enumerator(
            self.options.get("error-enumerator"))

    def _fork_api_spec(self, specs: Tuple[str, dict],
                       selected_namespaces: List[str]) -> dict:
        forked_specs = {}
        for name, spec in specs:
            # The new name of the component (e.g., class) is derived as follows
            # <pkg_name>.<base_name>
            # where <pkg_name> stands for the package of the generated program
            # where <base_name> represents the base name of the component
            # as taken from the input API.
            new_name = self.package_name + "." + get_base_api_name(
                name, self.api_docs)
            spec_str = json.dumps(spec)
            spec_str = functools.reduce(lambda acc, x: re.sub(
                re.compile(x.replace(".", "\\.") + "(?![A-Za-z.])"),
                self.package_name + "." + get_base_api_name(x, self.api_docs),
                acc), selected_namespaces, spec_str)
            new_spec = json.loads(spec_str)
            forked_specs[new_name] = new_spec
        return forked_specs

    def _handle_nested_classes(self, specs: List[dict],
                               included_ns: List[str]) -> set:
        included_libs = set()
        for cls_name, spec in list(specs):
            # If the current namespace has parent classes, include these
            # classes to our list of specs.
            for parent in get_parent_classes(cls_name, self.api_docs):
                if parent not in included_ns:
                    specs.append((parent, self.api_docs[parent]))
                    included_ns.append(parent)
            # Now handle any nested classes defined in the class given
            # by `cls_name`.
            for nested_c in get_nested_classes(cls_name, self.api_docs):
                if nested_c not in included_ns:
                    specs.append((nested_c, self.api_docs[nested_c]))
                    included_ns.append(nested_c)
                    t = (self.initial_api_graph.get_type_by_name(nested_c) or
                         self.parse_builtin_type(nested_c))
                    # Get the supertypes of ns
                    included_libs.update({
                        st.name for st in t.get_supertypes()
                        if st != self.bt_factory.get_any_type()})
        return included_libs

    def _add_missing_api_specs(self, forked_spec: dict):
        for k, v in self.api_docs.items():
            if k in forked_spec:
                continue
            copied_v = deepcopy(v)
            # For performance reasons, we don't include the specification
            # of the included methods.
            copied_v["methods"] = []
            copied_v["fields"] = []
            forked_spec[k] = copied_v

    def fork_api_spec(self, ns: str) -> dict:
        """
        Given a namespace (e.g., a class name), we find all the namespaces
        (e.g., classes) related to that (e.g., parent-child relationships).
        Then, we fork parts of the given API by changing the names of the
        given namespace `ns` and the related namespaces.
        """
        # We might encounter a namespace of the form: java.lang.Integer
        t = (self.initial_api_graph.get_type_by_name(ns) or
             self.parse_builtin_type(ns))
        # Get the supertypes of ns
        supertypes = {
            st for st in t.get_supertypes()
            if st != self.bt_factory.get_any_type()}
        # Note that we omit parent classes not defined in the given API.
        # We do that because their specification is not available to us.
        specs = [(ns, self.api_docs[ns])]
        specs.extend([(st.name, self.api_docs[st.name]) for st in supertypes
                     if st.name != ns and st.name in self.api_docs])
        all_names = [s[0] for s in specs]

        # Now select a random subset of namespaces. This means that our class
        # will inherit from some user-defined classes, but also inherit from
        # the classes that come from the given API.
        selected_namespaces = utils.random.sample(
            all_names, k=utils.random.integer(1, len(all_names)))
        if ns not in selected_namespaces:
            selected_namespaces.append(ns)
        specs = [s for s in specs if s[0] in selected_namespaces]
        included_libs = self._handle_nested_classes(specs, selected_namespaces)
        included_libs.update(all_names)
        forked_spec = self._fork_api_spec(specs, selected_namespaces)
        keys = forked_spec.keys()
        for elem in included_libs:
            if elem not in keys:
                forked_spec[elem] = self.api_docs[elem]
        self._add_missing_api_specs(forked_spec)
        return forked_spec

    def add_local_variables(self, m: ag.Method):
        t = self.api_graph.get_input_type(m)
        # Add this
        if t is not None:
            if t.is_type_constructor():
                t = t.new(t.type_parameters)
            self.api_graph.add_variable_node("this", t)
        # Add method's parameters
        for i, p in enumerate(m.parameters):
            param_type = p.t
            if p.variable:
                param_type = self.bt_factory.get_array_type().new([p.t])

            self.api_graph.add_variable_node(f"p{i}", param_type)

    def remove_local_variables(self, m: ag.Method):
        self.api_graph.remove_variable_node("this")
        for i in range(len(m.parameters)):
            self.api_graph.remove_variable_node(f"p{i}")

    def generate_super_call_unknown(self, m: ag.Constructor,
                                    parent_name: str) -> ast.FunctionCall:
        """
        This is a heuristic: We are trying to call the constructor of a call
        for which we don't have its specification. As a result, we know nothing
        about its constructors.

        We proceed as follows: If the the child class has a single constructor,
        then call the super constructor by assuming that the super constructor
        has the same signature as the child constructor.

        If the child class has multiple constructors, then assume that the
        parent constructor has the same signature with the child constructor
        that has the smallest arity.
        """
        constructors = [n for n in self.api_graph.get_api_nodes()
                        if isinstance(n, ag.Constructor) and n.name == m.name]
        if not constructors:
            return ast.FunctionCall(ast.FunctionCall.SUPER, [])
        if len(constructors) == 1:
            super_args = [self.generate_expr(p.t) for p in m.parameters]
            return ast.FunctionCall(ast.FunctionCall.SUPER,
                                    args=super_args)
        con = sorted(constructors, key=lambda x: len(x.parameters))[0]
        if con == m:
            # The constructor 'con' is the same with m. We avoid generating
            # a this call to avoid cycles.
            return None
        super_args = [self.generate_expr(p.t) for p in con.parameters]
        return ast.FunctionCall(ast.FunctionCall.THIS,
                                super_args)

    def generate_this_call(self, m: ag.Constructor):
        primary_constructors = [
            n for n in self.api_graph.get_api_nodes()
            if (isinstance(n, ag.Constructor) and n.name == m.name and
                n.metadata.get("primary", False) and n != m)
        ]
        if not primary_constructors or m.metadata.get("primary", False):
            return None
        primary_constructor = primary_constructors[0]
        super_args = [self.generate_expr(p.t)
                      for p in primary_constructor.parameters]
        return ast.FunctionCall(ast.FunctionCall.THIS,
                                super_args)

    def generate_super_call(self, m: ag.Constructor):
        st_names = [st.name
                    for st in self.api_graph.get_type_by_name(m.name).supertypes
                    if st != self.bt_factory.get_any_type()]
        parent_classes = [
            self.api_spec.get(n, n) for n in st_names
            if not is_parent_interface(m.get_class_name(), n, self.api_spec)
        ]
        if not parent_classes:
            # We don't need to generate a super call.
            return None
        parent_cls = parent_classes[0]
        if isinstance(parent_cls, str):
            # This means that we don't have the spec for the parent class.
            # Therefore, we know nothing about its constructors. Below, we
            # use a heuristic to a constructor a super call to an unknown
            # class.
            return self.generate_super_call_unknown(m, parent_cls)
        super_constructors = [
            node
            for node in self.api_graph.get_api_nodes()
            if isinstance(node, ag.Constructor) and node.name == parent_cls["name"]
        ]
        if not super_constructors:
            # The parent class does not define any constructor.
            return None
        super_constructor = utils.random.choice(super_constructors)
        super_args = [
            self.generate_expr(p.t)
            for p in super_constructor.parameters
        ]
        return ast.FunctionCall(
            ast.FunctionCall.SUPER,
            args=super_args
        )

    def convert_constructor(self, m: ag.Constructor,
                            ns_spec: dict) -> ast.Constructor:
        body = []
        this_call = self.generate_this_call(m)
        if this_call is not None:
            body.append(this_call)
        else:
            super_call = self.generate_super_call(m)
            if super_call is not None:
                body.append(super_call)
        return ast.Constructor(
            class_name=ns_spec["name"],
            params=[
                ast.ParameterDeclaration(f"p{i}", p.t, vararg=p.variable)
                for i, p in enumerate(m.parameters)
            ],
            body=ast.Block(body)
        )

    def generate_expr_from_special_method(self, m: ag.Method,
                                          depth: int,
                                          type_var_map: dict) -> ast.Expr:
        parameters = [param.t for param in m.parameters]
        args = self._generate_args(parameters,
                                   [[p] for p in parameters],
                                   depth + 1, type_var_map)
        converters = {
            "&&": lambda args: ast.LogicalExpr(args[0].expr, args[1].expr,
                                               ast.Operator("&&")),
            "||": lambda args: ast.LogicalExpr(args[0].expr, args[1].expr,
                                               ast.Operator("||")),
            "==": lambda args: ast.EqualityExpr(args[0].expr, args[1].expr,
                                                ast.Operator("==")),
            "!=": lambda args: ast.EqualityExpr(args[0].expr, args[1].expr,
                                                ast.Operator("=", is_not=True)),
            "+": lambda args: ast.ArithExpr(args[0].expr, args[1].expr,
                                            ast.Operator("+")),
            "-": lambda args: ast.ArithExpr(args[0].expr, args[1].expr,
                                            ast.Operator("-")),
            "/": lambda args: ast.ArithExpr(args[0].expr, args[1].expr,
                                            ast.Operator("/")),
            "*": lambda args: ast.ArithExpr(args[0].expr, args[1].expr,
                                            ast.Operator("*")),
            ">": lambda args: ast.ComparisonExpr(args[0].expr, args[1].expr,
                                                 ast.Operator(">")),
            "<": lambda args: ast.ComparisonExpr(args[0].expr, args[1].expr,
                                                 ast.Operator(">")),
            ">=": lambda args: ast.ComparisonExpr(args[0].expr, args[1].expr,
                                                  ast.Operator(">=")),
            "<=": lambda args: ast.ComparisonExpr(args[0].expr, args[1].expr,
                                                  ast.Operator("<=")),
            "?:": lambda args: ast.BinaryExpr(args[0].expr, args[1].expr,
                                              ast.Operator("?:")),
            "[]": lambda args: ast.BinaryExpr(args[0].expr, args[1].expr,
                                              ast.Operator("[]", wrap=True)),
            "_if_": lambda args: ast.Conditional(
                args[0].expr, args[1].expr, args[2].expr,
                inferred_type=(args[1].path[-1]
                               if args[2].path[-1].is_subtype(args[1].path[-1])
                               else args[2].path[-1])
            )
        }
        symbol = m.metadata["symbol"]
        expr = converters[symbol](args)
        out_type = self.api_graph.get_concrete_output_type(m)
        out_type = tp.substitute_type(out_type, type_var_map)
        expr.mk_typed(ast.TypePair(expected=self.peek_expected_type(),
                                   actual=out_type))
        return expr

    def _generate_expression_from_path(self, path: list, depth: int,
                                       type_var_map: dict) -> ast.Expr:

        elem = path[-1]
        if isinstance(elem, ag.Method) and elem.metadata.get("is_special"):
            return self.generate_expr_from_special_method(elem, depth,
                                                          type_var_map)
        else:
            return super()._generate_expression_from_path(path, depth,
                                                          type_var_map)

    def _generate_expr_from_node(self, node, depth=1, constraints=None):
        return super()._generate_expr_from_node(node, depth, constraints)

    def _get_mutable_local_vars(
        self,
        local_vars: List[ast.VariableDeclaration]
    ) -> List[ast.VariableDeclaration]:
        """
        Get a random sample of mutable local variables.
        """
        mutable_vars = [v for v in local_vars if not v.is_final]
        if not mutable_vars:
            return []
        mutable_vars = utils.random.sample(
            mutable_vars, k=utils.random.integer(0, len(mutable_vars)))
        return mutable_vars

    def _get_fields_of_local_var(
            self,
            local_var: ast.VariableDeclaration
    ) -> List[ag.Field]:
        cls = self.api_graph.get_type_by_name(local_var.get_type().name)
        fields = []
        if cls is not None:
            fields = [f for f in self.api_graph.get_neighbors_of_node(cls)
                      if (isinstance(f, ag.Field) and
                          not f.metadata.get("final", False))]
            return fields
        return []

    def generate_assignments(self, m: ag.Method,
                             local_vars: List[ast.VariableDeclaration]):
        """
        Generate a set of random assignments that mutate the value of the given
        local variables.
        """
        assignments = []
        mutable_vars = self._get_mutable_local_vars(local_vars)
        for local_var in mutable_vars:
            fields = self._get_fields_of_local_var(local_var)
            out_type = local_var.get_type()
            kwargs = {
                "name": local_var.name,
                "receiver": None,
            }
            if fields:
                f = utils.random.choice(fields)
                out_type = self.api_graph.get_concrete_output_type(f)
                sub = {}
                if local_var.get_type().is_parameterized():
                    sub = local_var.get_type().get_type_variable_assignments()
                out_type = tp.substitute_type(out_type, sub)
                receiver = ast.Variable(local_var.name)
                receiver.mk_typed(ast.TypePair(expected=None,
                                               actual=local_var.get_type()))
                kwargs.update({
                    "name": f.name,
                    "receiver": receiver
                })
            expr = self.generate_expr(out_type)
            kwargs["expr"] = expr
            assignments.append(ast.Assignment(**kwargs))
        return assignments

    def add_control_flow(self, block: ast.Block, block_type: tp.Type):
        if not isinstance(block, ast.Block) or not block.body:
            return block
        body_block = [block.body[-1]]
        for node in block.body[len(block.body) - 2::-1]:
            # FIXME: This probability should be an option.
            if utils.random.bool(prob=0.2):
                self.block_variables = True
                self.type_eraser.with_target(
                    self.bt_factory.get_boolean_type(primitive=True))
                self.push_target_type(self.bt_factory.get_boolean_type(
                    primitive=True))
                cond_expr = self._generate_expr_from_node(
                    self.bt_factory.get_boolean_type(primitive=True),
                    depth=2)[0]
                self.type_eraser.reset_target_type()
                self.pop_target_type()
                self.block_variables = False
                base_expr = ast.Block(body_block[::-1])
                alt_expr = self.generate_expr(block_type)
                true_expr, false_expr = ((base_expr, alt_expr)
                                         if utils.random.bool()
                                         else (alt_expr, base_expr))
                cond = ast.Conditional(
                    cond_expr, true_expr, false_expr,
                    inferred_type=block_type,
                    is_expression=False
                )
                body_block = [cond, node]
            else:
                # We put the node to the current block.
                body_block.append(node)
        return ast.Block(body_block[::-1])

    def _skip_method_creation(self, m: ag.Method) -> bool:
        blacklisted_obj_methods = self.api_graph.OBJECT_METHODS[self.language]
        params = blacklisted_obj_methods.get(m.name)
        if params is None:
            return False
        return (params == [p.t.name for p in m.parameters] and
                not m.metadata.get("static"))

    def convert_method(self, m: ag.Method,
                       ns_spec: dict) -> ast.FunctionDeclaration:
        out_type = self.api_graph.get_concrete_output_type(m)
        input_type = self.api_graph.get_input_type(m)
        assert out_type is not None
        func_name = m.name
        if "." in func_name:
            func_name = func_name.rsplit(".", 1)[1]
        if self._skip_method_creation(m):
            # If it's a common object method, then do not re-define it.
            return None
        is_abstract = is_definition_abstract(ns_spec, m)
        prev_ns = self.namespace
        self.namespace += (to_namespace(m),)
        body = None
        self.api_graph.add_types(m.type_parameters)
        self.add_local_variables(m)
        if not is_abstract:
            self.type_eraser.with_target(out_type)
            self.push_target_type(out_type)
            expr = self._generate_expr_from_node(out_type, 1)[0]
            self.pop_target_type()
            decls = list(self.context.get_declarations(self.namespace,
                                                       True).values())
            var_decls = [d for d in decls
                         if not isinstance(d, ast.ParameterDeclaration)]

            assignments = self.generate_assignments(m, var_decls)
            body = expr if not var_decls else ast.Block(
                var_decls + assignments + [expr])
            body = self.add_control_flow(body, out_type)
            self.type_eraser.reset_target_type()
            self.pop_target_type()
        self.remove_local_variables(m)
        func = ast.FunctionDeclaration(
            name=func_name,
            params=[
                ast.ParameterDeclaration(f"p{i}", p.t, vararg=p.variable)
                for i, p in enumerate(m.parameters)
            ],
            type_parameters=m.type_parameters,
            func_type=ast.FunctionDeclaration.CLASS_METHOD,
            ret_type=out_type,
            body=body,
            is_final=m.metadata.get("final", False),
            override=self.api_graph.is_method_overriden(input_type, m),
            metadata=m.metadata
        )
        self.namespace = prev_ns
        self.api_graph.remove_types(m.type_parameters)
        return func

    def convert_field(self, f: ag.Field,
                      ns_spec: dict) -> ast.FieldDeclaration:
        field_type = self.api_graph.get_concrete_output_type(f)
        receiver = self.api_graph.get_input_type(f)
        assert field_type is not None
        field_name = f.name
        if "." in field_name:
            field_name = field_name.rsplit(".", 1)[1]
        is_abstract = is_definition_abstract(ns_spec, f)
        if is_abstract:
            f.metadata["abstract"] = True
        return ast.FieldDeclaration(
            field_name, field_type,
            is_final=f.metadata.get("final", False),
            can_override=f.metadata.get("open", False),
            override=self.api_graph.is_field_overriden(receiver, f),
            metadata=f.metadata
        )

    def convert_node_to_decl(self, node: ag.APINode,
                             ns_spec: dict) -> ast.Declaration:
        converters = {
            ag.Method: self.convert_method,
            ag.Field: self.convert_field,
            ag.Constructor: self.convert_constructor,
        }
        return converters[type(node)](node, ns_spec)

    def create_components_of_namespace(self, ns: str, ns_spec: dict,
                                       class_type: tp.Type):
        """
        Generate all components that reside in the given namespace `ns`.
        These components are either methods, fields/variables, or constructors.
        """
        t = self.api_graph.get_type_by_name(ns)
        if t == self.bt_factory.get_any_type() or t is None:
            return []
        variables, methods, constructors = [], [], []
        for n in self.api_graph.get_neighbors_of_node(t):
            decl = self.convert_node_to_decl(n, ns_spec)
            if decl is None:
                continue
            if isinstance(decl, ast.FunctionDeclaration):
                methods.append(decl)
            else:
                variables.append(decl)

        # Temporarily remove class type parameters from the context, because
        # we need to deal with non-instance methods.
        if class_type.is_type_constructor():
            self.api_graph.remove_types(class_type.type_parameters)
        # Now consider static methods, static fields, or constructors
        for n in list(self.api_graph.get_api_nodes()):
            if isinstance(n, (ag.Method, ag.Field, ag.Constructor)):
                is_con = isinstance(n, ag.Constructor)
                is_static = ns == n.name.rsplit(".", 1)[0] and not is_con
                is_constructor = is_con and ns == n.name
                if is_static or is_constructor:
                    decl = self.convert_node_to_decl(n, ns_spec)
                    if decl is None:
                        continue
                    if isinstance(decl, ast.FunctionDeclaration):
                        methods.append(decl)
                    elif isinstance(decl, ast.Constructor):
                        constructors.append(decl)
                    else:
                        variables.append(decl)
        if class_type.is_type_constructor():
            self.api_graph.add_types(class_type.type_parameters)
        return variables, methods, constructors

    def create_class_from_spec(self, api_spec: dict, class_type: tp.Type):
        cls_name = class_type.name
        cls_spec = api_spec[cls_name]
        parent_namespace = get_namespace_from_name(cls_name, api_spec)
        self.namespace = parent_namespace + (cls_name,)
        if class_type.is_type_constructor():
            self.api_graph.add_types(class_type.type_parameters)
        # Get the fields and methods included in this current class.
        fields, methods, constructors = self.create_components_of_namespace(
            cls_name, cls_spec, class_type)
        self.namespace = parent_namespace
        # Get any other declarations (e.g., nested classes) included in
        # this class.
        extra_decls = list(self.context.get_classes(
            parent_namespace + (cls_name,), only_current=True).values())
        # Some class metadata. The class is static if it is not defined in
        # the global namespace and its parent is None.
        metadata = {
            "static": (cls_spec["parent"] is None and
                       parent_namespace != ast.GLOBAL_NAMESPACE),
            "functional": cls_spec["functional_interface"],
        }
        cls = ast.ClassDeclaration(
            cls_name,
            superclasses=[ast.SuperClassInstantiation(st)
                          for st in class_type.supertypes
                          if st != self.bt_factory.get_any_type()],
            class_type=cls_spec["class_type"],
            type_parameters=(class_type.type_parameters
                             if class_type.is_type_constructor() else []),
            functions=methods,
            fields=fields,
            constructors=constructors,
            is_final=False,
            extra_declarations=extra_decls, metadata=metadata
        )
        if class_type.is_type_constructor():
            self.api_graph.remove_types(class_type.type_parameters)
        # Now, add the class and its components to the context.
        self.context.add_class(parent_namespace, cls.name, cls)
        for field in fields:
            self.context.add_var(parent_namespace + (cls.name,),
                                 field.name, field)
        for method in methods:
            self.context.add_func(parent_namespace + (cls.name,),
                                  method.name, method)

    def create_program_from_spec(self, api_spec: dict,
                                 defined_namespaces: List[str]):
        self.context = Context()
        for name in sorted(defined_namespaces, reverse=True):
            t = self.api_graph.get_type_by_name(name)
            if t == self.bt_factory.get_any_type() or t is None:
                continue
            self.api_spec = api_spec
            self.create_class_from_spec(api_spec, t)
            self.api_spec.update(self.api_docs)
        return ast.Program(self.context, self.language, lib=self.api_spec)

    @log_onerror
    def generate_well_typed_program(self, api_namespace: str,
                                    program_id: int) -> ast.Program:
        """
        Generates a well-typed program from the given API namespace.
        """
        forked_spec = self.fork_api_spec(api_namespace)
        forked_spec.update(GROOVY_SPECIAL_METHODS)
        forked_spec.update(get_extra_api_components(
            self.api_docs, lambda x: x.get("functional_interface", False)))
        # This is the list of namespaces that are explicitly defined in
        # the program, i.e., they reside in the pakcage specified by
        # `self.package_name`.
        defined_namespaces = [
            k for k in forked_spec.keys()
            if k.startswith(self.package_name)
        ]
        api_builder = self.API_GRAPH_BUILDERS[self.language](
            self.language, **self.options)
        api_builder.parsed_types = self.api_builder.parsed_types
        self.api_graph = api_builder.build(forked_spec)
        program = self.create_program_from_spec(forked_spec,
                                                defined_namespaces)
        msg = (f"Generated skeleton program {program_id} using "
               "namespace {api_namespace}")
        log(self.logger, msg)
        return program

    def generate_ill_typed_programs(self, program: ast.Program,
                                    program_id: int):
        """
        Generates all ill-typed programs that stem from the given well-typed
        one.
        """
        # We first attempt to compile the program. The program is expected
        # to compile. If this is not the case, then there's no need
        # to proceed with error enumeration.
        succeeded, _ = compile_program(
            self.bt_factory.get_language(), program,
            self.package_name,
            library_path=self.options.get("library-path"))
        if not succeeded:
            log(self.logger,
                f"Skeleton program {program_id} unexpectedly does not compile")
            return None
        error_enum = self.ErrorEnumerator(program, self,
                                          self.bt_factory)
        flag = False
        try:
            for j, p in enumerate(error_enum.enumerate_programs()):
                if p is not None:
                    flag = True
                    self.error_injected = error_enum.error_explanation
                    msg = (f"Enumerating error program {j + 1}"
                           f" for skeleton {program_id}\n")
                    log(self.logger, msg)
                    log(self.logger, self.error_injected)
                    yield p
            if not flag:
                msg = f"No error added to skeleton {program_id}"
                log(self.logger, msg)
        except Exception as exc:
            log_error(self.logger, exc)

    def compute_programs(self) -> ast.Program:
        for i, api_namespace in enumerate(self.api_namespaces):
            program_id = i + 1
            program = self.generate_well_typed_program(api_namespace,
                                                       program_id)

            if program is None:
                continue
            if not self.ErrorEnumerator:
                yield program  # This is a well-typed program
            else:
                # Enumerate all ill-typed programs that stem from the given
                # skeleton program.
                yield from self.generate_ill_typed_programs(program,
                                                            program_id)

    def has_next(self) -> bool:
        return self._has_next

    def prepare_next_program(self, program_id, package_name):
        self.context = Context()
        self.error_injected = None
        self.package_name = package_name
