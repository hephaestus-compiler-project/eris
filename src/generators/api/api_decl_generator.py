from copy import deepcopy

from src.ir import ast, types as tp, type_utils as tu
from src.ir.context import Context
from src.generators import Generator, generators as gens
from src.generators.api import builder, matcher, api_graph as ag


class APIDeclarationGenerator(Generator):
    API_GRAPH_BUILDERS = {
        "java": builder.JavaAPIGraphBuilder,
        "kotlin": builder.KotlinAPIGraphBuilder,
        "groovy": builder.JavaAPIGraphBuilder,
        "scala": builder.ScalaAPIGraphBuilder,
    }

    def __init__(self, api_docs, options={}, language=None, logger=None):
        super().__init__(language=language, logger=logger)
        self.api_docs = api_docs
        self.api_graph: ag.APIGraph = self.API_GRAPH_BUILDERS[language](
            language, **options).build(api_docs)
        api_rules_file = options.get("api-rules")
        kwargs = {}
        if api_rules_file:
            self.matcher = matcher.parse_rule_file(api_rules_file)
            kwargs["matcher"] = self.matcher
        # self.log_api_graph_statistics(**kwargs)
        self._has_next = True
        self.error_injected = None
        # FIXME: Handle constructors
        api_components = (ag.Field, ag.Method)
        self.api_nodes = (n for n in self.api_graph.get_api_nodes()
                          if isinstance(n, api_components))

    def generate(self, context=None) -> ast.Program:
        node = next(self.api_nodes)
        while node is not None and (self.matcher and
                                    not self.matcher.match(node)):
            node = next(self.api_nodes)
        if node is None:
            self._has_next = False
            return None

        program = self.convert_node_to_program(node)
        return program

    def has_next(self) -> bool:
        return self._has_next

    def generate_expr(self,
                      expr_type: tp.Type = None,
                      only_leaves=False,
                      subtype=True,
                      exclude_var=False,
                      gen_bottom=False,
                      sam_coercion=False) -> ast.Expr:
        void_type = type(self.bt_factory.get_void_type())
        if isinstance(expr_type, void_type) and getattr(expr_type, "primitive",
                                                        False):
            # For primitive void we generate an empty block
            return ast.Block(body=[])
        assert expr_type is not None
        constant_candidates = {
            self.bt_factory.get_number_type().name: gens.gen_integer_constant,
            self.bt_factory.get_integer_type().name: gens.gen_integer_constant,
            self.bt_factory.get_big_integer_type().name: gens.gen_integer_constant,
            self.bt_factory.get_byte_type().name: gens.gen_integer_constant,
            self.bt_factory.get_short_type().name: gens.gen_integer_constant,
            self.bt_factory.get_long_type().name: gens.gen_integer_constant,
            self.bt_factory.get_float_type().name: gens.gen_real_constant,
            self.bt_factory.get_double_type().name: gens.gen_real_constant,
            self.bt_factory.get_big_decimal_type().name: gens.gen_real_constant,
            self.bt_factory.get_char_type().name: gens.gen_char_constant,
            self.bt_factory.get_string_type().name: gens.gen_string_constant,
            self.bt_factory.get_boolean_type().name: gens.gen_bool_constant,
            self.bt_factory.get_array_type().name: (
                lambda x: self.gen_array_expr(
                    tu.substitute_invariant_wildcard_with(
                        x, [self.bt_factory.get_any_type()]
                    ),
                    only_leaves=True, subtype=False)
            ),
        }
        generator = constant_candidates.get(expr_type.name.capitalize())
        if generator is not None:
            return generator(expr_type)
        else:
            return ast.BottomConstant(expr_type)

    def convert_type_to_class(self, t: tp.Type) -> ast.ClassDeclaration:
        if t is None:
            return None
        assert not t.is_type_var()
        type_parameters = []
        if t.is_parameterized():
            type_parameters.extend(t.t_constructor.type_parameters)
        if t.is_type_constructor():
            type_parameters.extend(t.type_parameters)
        class_type = self.api_docs[t.name]["class_type"]
        return ast.ClassDeclaration(
            t.name.rsplit(".", 1)[1],
            superclasses=[],
            class_type=class_type,
            type_parameters=type_parameters,
            functions=[],
            fields=[],
            is_final=False
        )

    def convert_method(self, m: ag.Method,
                       is_parent_abstract: bool) -> ast.FunctionDeclaration:
        out_type = self.api_graph.get_concrete_output_type(m)
        assert out_type is not None
        func_name = m.name
        if "." in func_name:
            func_name = func_name.rsplit(".", 1)[1]
        is_abstract = (not m.metadata.get("static", False) and
                       not m.metadata.get("default", False) and
                       (is_parent_abstract or
                        m.metadata.get("is_abstract", False)))
        func = ast.FunctionDeclaration(
            name=func_name,
            params=[
                ast.ParameterDeclaration(f"p{i}", p.t, vararg=p.variable)
                for i, p in enumerate(m.parameters)
            ],
            type_parameters=m.type_parameters,
            func_type=ast.FunctionDeclaration.CLASS_METHOD,
            ret_type=out_type,
            body=None if is_abstract else self.generate_expr(out_type),
            is_final=False,
            override=False,
            **m.metadata
        )
        return func

    def convert_field(self, f: ag.Field,
                      is_parent_abstract: bool) -> ast.FieldDeclaration:
        field_type = self.api_graph.get_concrete_output_type(f)
        assert field_type is not None
        field_name = f.name
        if "." in field_name:
            field_name = field_name.rsplit(".", 1)[1]
        return ast.FieldDeclaration(field_name, field_type, is_final=False,
                                    can_override=True, override=False)

    def convert_node_to_decl(self, node: ag.APINode,
                             is_parent_abstract: bool) -> ast.Declaration:
        converters = {
            ag.Method: self.convert_method,
            ag.Field: self.convert_field,
        }
        return converters[type(node)](node, is_parent_abstract)

    def add_decl_to_parent(self, context: Context, parent: ast.Declaration,
                           child: ast.Declaration):
        parent_namespace = ast.GLOBAL_NAMESPACE + (parent.name,)
        if isinstance(child, ast.FunctionDeclaration):
            parent.functions.append(child)
            context.add_func(parent_namespace, child.name, child)
        if isinstance(child, ast.FieldDeclaration):
            parent.fields.append(child)
            context.add_var(parent_namespace, child.name, child)

    def convert_node_to_program(self, node: ag.APINode) -> ast.Program:
        context = Context()
        rec_type = self.api_graph.get_input_type(node)
        cls = self.convert_type_to_class(rec_type)
        if cls is None and node.cls is not None:
            # This is a static method/field
            cls_type = self.api_graph.get_type_by_name(node.cls)
            cls = self.convert_type_to_class(cls_type)
        context.add_class(ast.GLOBAL_NAMESPACE, cls.name, cls)
        is_parent_abstract = (cls and
                              cls.class_type == ast.ClassDeclaration.INTERFACE)
        decl = self.convert_node_to_decl(node, is_parent_abstract)
        self.add_decl_to_parent(context, cls, decl)
        return ast.Program(deepcopy(context), self.language)
