from copy import deepcopy

from src.ir import ast, types as tp
from src.ir.context import Context
from src.generators import Generator
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

    def generate(self, context=None) -> ast.Program:
        api_components = (ag.Field, ag.Constructor, ag.Method)
        api_nodes = (n for n in self.api_graph.get_api_nodes()
                     if isinstance(n, api_components))
        node = next(api_nodes)
        while node is not None and (self.matcher and
                                    not self.matcher.match(node)):
            node = next(api_nodes)
        if node is None:
            self._has_next = False
            return None

        program = self.convert_node_to_program(node)
        return program

    def has_next(self) -> bool:
        return self._has_next

    def convert_type_to_class(self, t: tp.Type) -> ast.ClassDeclaration:
        if t is None:
            return None
        assert not t.is_type_var()
        type_parameters = []
        if t.is_parameterized():
            type_parameters.extend([t.t_constructor.type_parameters])
        return ast.ClassDeclaration(
            t.name.rsplit(".", 1)[1],
            superclasses=[],
            class_type=ast.ClassDeclaration.REGULAR,
            type_parameters=type_parameters
        )

    def convert_method(self, m: ag.Method) -> ast.FunctionDeclaration:
        out_type = self.api_graph.get_output_type(m)
        assert out_type is not None
        func_name = m.name
        if "." in func_name:
            func_name = func_name.rsplit(".", 1)[1]
        func = ast.FunctionDeclaration(
            name=func_name,
            params=[
                ast.ParameterDeclaration(f"p{i}", p.t, vararg=p.variable)
                for i, p in enumerate(m.parameters)
            ],
            type_parameters=m.type_parameters,
            func_type=ast.FunctionDeclaration.CLASS_METHOD,
            ret_type=out_type,
            body=ast.BottomConstant(out_type)
        )
        return func

    def convert_field(self, f: ag.Field) -> ast.FieldDeclaration:
        raise NotImplementedError

    def convert_node_to_decl(self, node: ag.APINode) -> ast.Declaration:
        converters = {
            ag.Method: self.convert_method,
            ag.Field: self.convert_field,
        }
        return converters[type(node)](node)

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
        context.add_class(ast.GLOBAL_NAMESPACE, cls.name, cls)
        decl = self.convert_node_to_decl(node)
        self.add_decl_to_parent(context, cls, decl)
        return ast.Program(deepcopy(context), self.language)
