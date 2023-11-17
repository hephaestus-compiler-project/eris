import json
import functools
import re

from src.ir import ast, types as tp, type_utils as tu
from src.ir.context import Context
from src.generators import generators as gens
from src.generators.api import builder, api_graph as ag
from src.generators.api.api_generator import APIClientGenerator


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
        self.api_docs = api_docs
        self.package_name = None
        self.api_namespaces = iter(api_docs.keys())

    def fork_api_spec(self, ns: str):
        supertypes = {
            st for st in self.api_graph.get_type_by_name(ns).get_supertypes()
            if st != self.bt_factory.get_any_type()}
        specs = [(st.name, self.api_docs[st.name]) for st in supertypes]
        all_names = [s[0] for s in specs]
        forked_specs = {}
        for name, spec in specs:
            new_name = self.package_name + "." + name.rsplit(".", 1)[1]
            spec_str = json.dumps(spec)
            spec_str = functools.reduce(lambda acc, x: re.sub(
                re.compile(x.replace(".", "\\.") + "(?![A-Za-z])"),
                self.package_name + "." + x.rsplit(".", 1)[-1], acc),
                                        all_names, spec_str)
            new_spec = json.loads(spec_str)
            forked_specs[new_name] = new_spec
        return forked_specs

    def convert_method(self, m: ag.Method,
                       api_graph: ag.APIGraph,
                       is_parent_abstract: bool) -> ast.FunctionDeclaration:
        out_type = api_graph.get_concrete_output_type(m)
        assert out_type is not None
        func_name = m.name
        if "." in func_name:
            func_name = func_name.rsplit(".", 1)[1]
        is_abstract = (not m.metadata.get("static", False) and
                       not m.metadata.get("default", False) and
                       (is_parent_abstract or
                        m.metadata.get("is_abstract", False)))
        prev_ns = self.namespace
        self.namespace += (func_name,)
        func = ast.FunctionDeclaration(
            name=func_name,
            params=[
                ast.ParameterDeclaration(f"p{i}", p.t, vararg=p.variable)
                for i, p in enumerate(m.parameters)
            ],
            type_parameters=m.type_parameters,
            func_type=ast.FunctionDeclaration.CLASS_METHOD,
            ret_type=out_type,
            body=None if is_abstract else self._generate_expr_from_node(out_type, 2)[0],
            is_final=False,
            override=False,
            **m.metadata
        )
        self.namespace = prev_ns
        return func

    def convert_field(self, f: ag.Field,
                      api_graph: ag.APIGraph,
                      is_parent_abstract: bool) -> ast.FieldDeclaration:
        field_type = api_graph.get_concrete_output_type(f)
        assert field_type is not None
        field_name = f.name
        if "." in field_name:
            field_name = field_name.rsplit(".", 1)[1]
        return ast.FieldDeclaration(field_name, field_type, is_final=False,
                                    can_override=True, override=False)

    def convert_node_to_decl(self, node: ag.APINode,
                             api_graph: ag.APIGraph,
                             is_parent_abstract: bool) -> ast.Declaration:
        converters = {
            ag.Method: self.convert_method,
            ag.Field: self.convert_field,
        }
        return converters[type(node)](node, api_graph, is_parent_abstract)

    def create_methods_from_namespace(self, ns: str, api_graph: ag.APIGraph,
                                      is_parent_abstract):
        t = api_graph.get_type_by_name(ns)
        if t == self.bt_factory.get_any_type() or t is None:
            return []
        fields, methods = [], []
        for n in api_graph.get_neighbors_of_node(t):
            if isinstance(n, ag.Constructor):
                # FIXME support constructors
                continue
            decl = self.convert_node_to_decl(n, api_graph, is_parent_abstract)
            if isinstance(decl, ast.FieldDeclaration):
                fields.append(decl)
            else:
                methods.append(decl)
        return fields, methods

    def create_program_from_spec(self, api_spec: dict, api_graph: ag.APIGraph,
                                 defined_classes: list):
        self.context = Context()
        for name in defined_classes:
            spec = api_spec[name]
            t = api_graph.get_type_by_name(name)
            if t == self.bt_factory.get_any_type():
                continue
            if t is None:
                continue
            is_parent_abstract = spec["class_type"] == ast.ClassDeclaration.INTERFACE
            self.namespace = ast.GLOBAL_NAMESPACE + (name,)
            fields, methods = self.create_methods_from_namespace(
                name, api_graph, is_parent_abstract)
            self.namespace = ast.GLOBAL_NAMESPACE
            cls = ast.ClassDeclaration(
                name,
                superclasses=[ast.SuperClassInstantiation(st)
                              for st in t.supertypes
                              if st != self.bt_factory.get_any_type()],
                class_type=spec["class_type"],
                type_parameters=(
                    t.type_parameters if t.is_type_constructor() else []),
                functions=methods,
                fields=fields,
                is_final=False
            )
            self.context.add_class(ast.GLOBAL_NAMESPACE, cls.name, cls)
            for field in fields:
                self.context.add_var(ast.GLOBAL_NAMESPACE + (cls.name,),
                                     field.name, field)
            for method in methods:
                self.context.add_func(ast.GLOBAL_NAMESPACE + (cls.name,),
                                      method.name, method)
        return ast.Program(self.context, self.language)

    def generate(self, context=None) -> ast.Program:
        try:
            api_namespace = next(self.api_namespaces)
            forked_spec = self.fork_api_spec(api_namespace)
            defined_classes = list(forked_spec.keys())
            forked_spec.update(self.api_docs)
            api_graph = self.API_GRAPH_BUILDERS[self.language](
                self.language, **self.options).build(forked_spec)
            program = self.create_program_from_spec(forked_spec, api_graph,
                                                    defined_classes)
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
