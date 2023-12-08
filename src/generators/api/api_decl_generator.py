import json
import functools
import re
from typing import List

from src import utils
from src.ir import ast, types as tp
from src.ir.context import Context
from src.generators.api import builder, api_graph as ag
from src.generators.api.api_generator import APIClientGenerator
from src.generators.api.special_methods import GROOVY_SPECIAL_METHODS


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


def to_namespace(m: ag.Method) -> str:
    param_str = ", ".join([p.t.name for p in m.parameters])
    func_name = m.name
    if "." in func_name:
        func_name = func_name.rsplit(".", 1)[1]
    return f"{func_name}({param_str})"


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
        self.api_namespaces = iter(utils.random.shuffle(
            [k for k in api_docs.keys()]))

    def fork_api_spec(self, ns: str):
        # Get the supertypes of ns
        supertypes = {
            st for st in self.initial_api_graph.get_type_by_name(
                ns).get_supertypes()
            if st != self.bt_factory.get_any_type()}
        specs = [(st.name, self.api_docs[st.name]) for st in supertypes]
        all_names = [s[0] for s in specs]
        # If the current namespace has parent classes, include these classes
        # to our list of specs.
        for cls_name, spec in specs:
            for parent in get_parent_classes(cls_name, self.api_docs):
                if parent not in all_names:
                    specs.append((parent, self.api_docs[parent]))
                    all_names.append(parent)

        # Now select a random subset of namespaces. This means that our class
        # will inherit from some user-defined classes, but also inherit from
        # the classes that come from the given API.
        selected_namespaces = utils.random.sample(
            all_names, k=utils.random.integer(1, len(all_names)))
        if ns not in selected_namespaces:
            selected_namespaces.append(ns)
        selected_namespaces.extend(x for x in all_names
                                   if any(x in y and x != y
                                          for y in selected_namespaces))
        specs = [s for s in specs if s[0] in selected_namespaces]
        forked_specs = {}
        for name, spec in specs:
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

    def add_local_variables(self, m: ag.Method):
        t = self.api_graph.get_input_type(m)
        # Add this
        if t is not None:
            if t.is_type_constructor():
                t = t.new(t.type_parameters)
            self.api_graph.add_variable_node("this", t)
        # Add method's parameters
        for i, p in enumerate(m.parameters):
            self.api_graph.add_variable_node(f"p{i}", p.t)

    def remove_local_variables(self, m: ag.Method):
        self.api_graph.remove_variable_node("this")
        for i in range(len(m.parameters)):
            self.api_graph.remove_variable_node(f"p{i}")

    def generate_super_call(self, m: ag.Constructor):
        st_names = [st.name
                    for st in self.api_graph.get_type_by_name(m.name).supertypes
                    if st != self.bt_factory.get_any_type()]
        parent_classes = [
            self.api_spec[n] for n in st_names
            if self.api_spec[n]["class_type"] != ast.ClassDeclaration.INTERFACE
        ]
        if not parent_classes:
            # We don't need to generate a super call.
            return None
        parent_cls = parent_classes[0]
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
            "_if_": lambda args: ast.Conditional(
                args[0].expr, args[1].expr, args[2].expr,
                inferred_type=(args[1].path[-1]
                               if args[2].path[-1].is_subtype(args[1].path[-1])
                               else args[2].path[-1])
            )
        }
        symbol = m.metadata["symbol"]
        return converters[symbol](args)

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

    def generate_assignments(self, m: ag.Method,
                             local_vars: List[ast.VariableDeclaration]):
        """
        Generate a set of random assignments that mutate the value of the given
        local variables.
        """
        mutable_vars = [v for v in local_vars if not v.is_final]
        if not mutable_vars:
            return []
        mutable_vars = utils.random.sample(
            mutable_vars, k=utils.random.integer(0, len(mutable_vars)))
        assignments = []
        for local_var in mutable_vars:
            cls = self.api_graph.get_type_by_name(local_var.get_type().name)
            fields = []
            if cls is not None:
                fields = [f for f in self.api_graph.get_neighbors_of_node(cls)
                          if (isinstance(f, ag.Field) and
                              not f.metadata.get("is_final", False))]
            out_type = local_var.get_type()
            kwargs = {
                "name": local_var.name,
                "receiver": None,
            }
            if fields:
                f = utils.random.choice(fields)
                out_type = self.api_graph.get_output_type(f)
                kwargs.update({
                    "name": f.name,
                    "receiver": ast.Variable(local_var.name)
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
            if utils.random.bool(prob=0.3):
                # We put the node to the current block.
                body_block.append(node)
            else:
                cond_expr = self.generate_expr(
                    self.bt_factory.get_boolean_type(primitive=True))
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
        return ast.Block(body_block[::-1])

    def convert_method(self, m: ag.Method,
                       ns_spec: dict) -> ast.FunctionDeclaration:
        out_type = self.api_graph.get_concrete_output_type(m)
        assert out_type is not None
        func_name = m.name
        if "." in func_name:
            func_name = func_name.rsplit(".", 1)[1]
        is_parent_abstract = (
            ns_spec.get("class_type") == ast.ClassDeclaration.INTERFACE)
        is_abstract = (not m.metadata.get("static", False) and
                       not m.metadata.get("default", False) and
                       (is_parent_abstract or
                        m.metadata.get("is_abstract", False)))
        prev_ns = self.namespace
        self.namespace += (to_namespace(m),)
        body = None
        self.api_graph.add_types(m.type_parameters)
        self.add_local_variables(m)
        if not is_abstract:
            expr = self._generate_expr_from_node(out_type, 1)[0]
            decls = list(self.context.get_declarations(self.namespace,
                                                       True).values())
            var_decls = [d for d in decls
                         if not isinstance(d, ast.ParameterDeclaration)]

            assignments = self.generate_assignments(m, var_decls)
            body = expr if not var_decls else ast.Block(
                var_decls + assignments + [expr])
            body = self.add_control_flow(body, out_type)
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
            is_final=False,
            override=False,
            **m.metadata
        )
        self.namespace = prev_ns
        self.api_graph.remove_types(m.type_parameters)
        return func

    def convert_field(self, f: ag.Field,
                      ns_spec: dict) -> ast.FieldDeclaration:
        field_type = self.api_graph.get_concrete_output_type(f)
        assert field_type is not None
        field_name = f.name
        if "." in field_name:
            field_name = field_name.rsplit(".", 1)[1]
        return ast.FieldDeclaration(field_name, field_type, is_final=False,
                                    can_override=True, override=False)

    def convert_node_to_decl(self, node: ag.APINode,
                             ns_spec: dict) -> ast.Declaration:
        converters = {
            ag.Method: self.convert_method,
            ag.Field: self.convert_field,
            ag.Constructor: self.convert_constructor,
        }
        return converters[type(node)](node, ns_spec)

    def create_components_of_namespace(self, ns: str, ns_spec: dict):
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
            if isinstance(decl, ast.FunctionDeclaration):
                methods.append(decl)
            else:
                variables.append(decl)
        # Now consider static methods, static fields, or constructors
        for n in list(self.api_graph.get_api_nodes()):
            if isinstance(n, (ag.Method, ag.Field, ag.Constructor)):
                is_static = ns == n.name.rsplit(".", 1)[0]
                is_constructor = (
                    isinstance(n, ag.Constructor) and
                    ns == n.name
                )
                if is_static or is_constructor:
                    decl = self.convert_node_to_decl(n, ns_spec)
                    if isinstance(decl, ast.FunctionDeclaration):
                        methods.append(decl)
                    elif isinstance(decl, ast.Constructor):
                        constructors.append(decl)
                    else:
                        variables.append(decl)
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
            cls_name, cls_spec)
        self.namespace = parent_namespace
        # Get any other declarations (e.g., nested classes) included in
        # this class.
        extra_decls = list(self.context.get_classes(
            parent_namespace + (cls_name,), only_current=True).values())
        # Some class metadata. The class is static if it is not defined in
        # the global namespace and its parent is None.
        metadata = {"is_static": (cls_spec["parent"] is None and
                                  parent_namespace != ast.GLOBAL_NAMESPACE)}
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
            extra_declarations=extra_decls, **metadata
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
            # This namespace corresponds to a class, because there's no type
            # with the same name as `name`.
            self.api_spec = api_spec
            self.create_class_from_spec(api_spec, t)
        return ast.Program(self.context, self.language, lib=api_spec)

    def generate(self, context=None) -> ast.Program:
        try:
            api_namespace = next(self.api_namespaces)
            forked_spec = self.fork_api_spec(api_namespace)
            # This is the list of namespaces that are explicitly defined in
            # the program.
            defined_namespaces = list(forked_spec.keys())
            forked_spec.update(self.api_docs)
            self.api_graph = self.API_GRAPH_BUILDERS[self.language](
                self.language, **self.options).build(forked_spec)
            program = self.create_program_from_spec(forked_spec,
                                                    defined_namespaces)
            return program
        except StopIteration:
            self._has_next = False
            return None

    def has_next(self) -> bool:
        return self._has_next

    def prepare_next_program(self, program_id, package_name):
        self.context = None
        self.error_injected = None
        self.package_name = package_name
