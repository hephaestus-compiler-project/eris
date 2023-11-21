import json
import functools
import re
from typing import List

from src.ir import ast, types as tp, type_utils as tu
from src.ir.context import Context
from src.generators.api import builder, api_graph as ag
from src.generators.api.api_generator import APIClientGenerator


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
        self.api_namespaces = iter(k for k in api_docs.keys())

    def fork_api_spec(self, ns: str):
        supertypes = {
            st for st in self.api_graph.get_type_by_name(ns).get_supertypes()
            if st != self.bt_factory.get_any_type()}
        specs = [(st.name, self.api_docs[st.name]) for st in supertypes]
        all_names = [s[0] for s in specs]
        for parent in get_parent_classes(ns, self.api_docs):
            if parent not in all_names:
                specs.append((parent, self.api_docs[parent]))
                all_names.append(parent)

        forked_specs = {}
        for name, spec in specs:
            new_name = self.package_name + "." + get_base_api_name(
                name, self.api_docs)
            spec_str = json.dumps(spec)
            spec_str = functools.reduce(lambda acc, x: re.sub(
                re.compile(x.replace(".", "\\.") + "(?![A-Za-z])"),
                self.package_name + "." + get_base_api_name(x, self.api_docs),
                acc), all_names, spec_str)
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
        body = None
        if not is_abstract:
            expr = self._generate_expr_from_node(out_type, 2)[0]
            decls = list(self.context.get_declarations(self.namespace,
                                                       True).values())
            var_decls = [d for d in decls
                         if not isinstance(d, ast.ParameterDeclaration)]
            body = expr if not var_decls else ast.Block(var_decls + [expr])
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

    def create_components_of_namespace(self, ns: str, api_graph: ag.APIGraph,
                                       is_parent_abstract):
        """
        Generate all components that reside in the given namespace `ns`.
        These components are either methods, fields/variables, or constructors.
        """
        t = api_graph.get_type_by_name(ns)
        if t == self.bt_factory.get_any_type() or t is None:
            return []
        variables, methods = [], []
        for n in api_graph.get_neighbors_of_node(t):
            if isinstance(n, ag.Constructor):
                # FIXME support constructors
                continue
            decl = self.convert_node_to_decl(n, api_graph, is_parent_abstract)
            if isinstance(decl, ast.FunctionDeclaration):
                methods.append(decl)
            else:
                variables.append(decl)
        return variables, methods

    def create_class_from_spec(self, api_spec: dict, api_graph: ag.APIGraph,
                               class_type: tp.Type):
        cls_name = class_type.name
        cls_spec = api_spec[cls_name]
        is_parent_abstract = (
            cls_spec.get("class_type") == ast.ClassDeclaration.INTERFACE)
        parent_namespace = get_namespace_from_name(cls_name, api_spec)
        self.namespace = parent_namespace + (cls_name,)
        fields, methods = self.create_components_of_namespace(
            cls_name, api_graph, is_parent_abstract)
        self.namespace = parent_namespace
        extra_decls = list(self.context.get_classes(
            parent_namespace + (cls_name,), only_current=True).values())
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
            is_final=False,
            extra_declarations=extra_decls
        )
        self.context.add_class(parent_namespace, cls.name, cls)
        for field in fields:
            self.context.add_var(parent_namespace + (cls.name,),
                                 field.name, field)
        for method in methods:
            self.context.add_func(parent_namespace + (cls.name,),
                                  method.name, method)

    def create_program_from_spec(self, api_spec: dict, api_graph: ag.APIGraph,
                                 defined_namespaces: List[str]):
        self.context = Context()
        for name in sorted(defined_namespaces, reverse=True):
            t = api_graph.get_type_by_name(name)
            if t == self.bt_factory.get_any_type() or t is None:
                continue
            # This namespace corresponds to a class, because there's no type
            # with the same name as `name`.
            self.create_class_from_spec(api_spec, api_graph, t)
        return ast.Program(self.context, self.language)

    def generate(self, context=None) -> ast.Program:
        try:
            api_namespace = next(self.api_namespaces)
            forked_spec = self.fork_api_spec(api_namespace)
            # This is the list of namespaces that are explicitly defined in
            # the program.
            defined_namespaces = list(forked_spec.keys())
            forked_spec.update(self.api_docs)
            api_graph = self.API_GRAPH_BUILDERS[self.language](
                self.language, **self.options).build(forked_spec)
            program = self.create_program_from_spec(forked_spec, api_graph,
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
