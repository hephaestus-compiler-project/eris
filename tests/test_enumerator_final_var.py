"""
Unit tests for the FinalVarErrorEnumerator.

Tests are organised into three groups:
  1. FinalVarAnalysis – the visitor that collects declarations and assignments.
  2. Enumeration logic – enumerate_programs yields correct variants.
  3. Metadata – locations and examined counts are set correctly.
"""
import pytest

from src.ir import ast, types as tp, java_types as jt
from src.ir.context import Context
# Pre-load generators package to avoid circular import.
from src.generators.api.builder import JavaAPIGraphBuilder  # noqa: F401
from src.enumerators.final_var_error import FinalVarAnalysis, FinalVarErrorEnumerator


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

INT_TYPE = jt.Integer


def make_bottom(t=INT_TYPE) -> ast.BottomConstant:
    expr = ast.BottomConstant(t)
    expr.mk_typed(ast.TypePair(expected=t, actual=t))
    return expr


def make_var_decl(name: str, is_final: bool = False) -> ast.VariableDeclaration:
    return ast.VariableDeclaration(
        name=name,
        expr=make_bottom(),
        is_final=is_final,
        var_type=INT_TYPE,
    )


def make_assignment(name: str, receiver=None) -> ast.Assignment:
    return ast.Assignment(name=name, expr=make_bottom(), receiver=receiver)


def make_field_decl(name: str, is_final: bool = False) -> ast.FieldDeclaration:
    return ast.FieldDeclaration(
        name=name,
        field_type=INT_TYPE,
        is_final=is_final,
    )


def make_func(name: str, body_stmts: list) -> ast.FunctionDeclaration:
    return ast.FunctionDeclaration(
        name=name,
        params=[],
        ret_type=jt.Void,
        body=ast.Block(body_stmts),
        func_type=ast.FunctionDeclaration.CLASS_METHOD,
        metadata={},
    )


def make_program(*decls) -> ast.Program:
    ctx = Context()
    program = ast.Program(ctx, "java")
    for d in decls:
        program.add_declaration(d)
    return program


def make_class(name: str, fields=None, functions=None,
               superclasses=None) -> ast.ClassDeclaration:
    return ast.ClassDeclaration(
        name=name,
        superclasses=superclasses or [],
        fields=fields or [],
        functions=functions or [],
    )


def make_superclass_inst(cls: ast.ClassDeclaration) -> ast.SuperClassInstantiation:
    return ast.SuperClassInstantiation(class_type=cls.get_type())


def make_enumerator(program: ast.Program) -> FinalVarErrorEnumerator:
    return FinalVarErrorEnumerator(
        program=program,
        program_gen=None,
        bt_factory=jt.JavaBuiltinFactory(),
    )


# ---------------------------------------------------------------------------
# 1. FinalVarAnalysis tests
# ---------------------------------------------------------------------------

class TestFinalVarAnalysis:

    def test_collects_non_final_var_decl(self):
        x = make_var_decl("x", is_final=False)
        func = make_func("test", [x])
        program = make_program(func)

        analysis = FinalVarAnalysis()
        analysis.visit(program)

        assert x in analysis.var_decls

    def test_does_not_collect_final_var_decl(self):
        x = make_var_decl("x", is_final=True)
        func = make_func("test", [x])
        program = make_program(func)

        analysis = FinalVarAnalysis()
        analysis.visit(program)

        assert x not in analysis.var_decls

    def test_collects_non_final_field_decl(self):
        field = make_field_decl("f", is_final=False)
        cls = make_class("A", fields=[field])
        program = make_program(cls)

        analysis = FinalVarAnalysis()
        analysis.visit(program)

        assert field in [fd for fd, _ in analysis.field_decls]

    def test_field_decl_paired_with_class_name(self):
        field = make_field_decl("f", is_final=False)
        cls = make_class("A", fields=[field])
        program = make_program(cls)

        analysis = FinalVarAnalysis()
        analysis.visit(program)

        assert ("f", "A") in [(fd.name, cn) for fd, cn in analysis.field_decls]

    def test_does_not_collect_final_field_decl(self):
        field = make_field_decl("f", is_final=True)
        cls = make_class("A", fields=[field])
        program = make_program(cls)

        analysis = FinalVarAnalysis()
        analysis.visit(program)

        assert field not in [fd for fd, _ in analysis.field_decls]

    def test_collects_all_assigned_names(self):
        assign = make_assignment("x")
        func = make_func("test", [assign])
        program = make_program(func)

        analysis = FinalVarAnalysis()
        analysis.visit(program)

        assert "x" in analysis.all_assigned_names

    def test_collects_field_assignment_in_class(self):
        assign = make_assignment("f")
        method = make_func("setF", [assign])
        cls = make_class("A", functions=[method])
        program = make_program(cls)

        analysis = FinalVarAnalysis()
        analysis.visit(program)

        assert "f" in analysis.class_assignments.get("A", set())

    def test_no_assignments_gives_empty_sets(self):
        x = make_var_decl("x", is_final=False)
        func = make_func("test", [x])
        program = make_program(func)

        analysis = FinalVarAnalysis()
        analysis.visit(program)

        assert analysis.all_assigned_names == set()

    def test_superclass_registered(self):
        parent = make_class("Parent")
        child = make_class("Child", superclasses=[make_superclass_inst(parent)])
        program = make_program(parent, child)

        analysis = FinalVarAnalysis()
        analysis.visit(program)

        assert "Parent" in analysis.superclasses.get("Child", [])

    def test_is_field_assigned_direct_class(self):
        """Assignment in the declaring class matches."""
        assign = make_assignment("f")
        method = make_func("setF", [assign])
        cls = make_class("A", functions=[method])
        program = make_program(cls)

        analysis = FinalVarAnalysis()
        analysis.visit(program)

        assert analysis.is_field_assigned("f", "A")

    def test_is_field_assigned_subclass(self):
        """Assignment in a subclass also matches the parent field."""
        assign = make_assignment("f")
        method = make_func("setF", [assign])
        parent = make_class("Parent")
        child = make_class("Child",
                           superclasses=[make_superclass_inst(parent)],
                           functions=[method])
        program = make_program(parent, child)

        analysis = FinalVarAnalysis()
        analysis.visit(program)

        assert analysis.is_field_assigned("f", "Parent")

    def test_is_field_not_assigned_unrelated_class(self):
        """Assignment in an unrelated class does not match."""
        assign = make_assignment("f")
        method = make_func("setF", [assign])
        cls_a = make_class("A", functions=[method])
        cls_b = make_class("B")  # no relation to A
        program = make_program(cls_a, cls_b)

        analysis = FinalVarAnalysis()
        analysis.visit(program)

        assert not analysis.is_field_assigned("f", "B")


# ---------------------------------------------------------------------------
# 2. Enumeration tests
# ---------------------------------------------------------------------------

class TestFinalVarErrorEnumerator:

    def test_no_variants_when_no_assignments(self):
        """Non-final variable with no assignment → no variants."""
        x = make_var_decl("x", is_final=False)
        func = make_func("test", [x])
        program = make_program(func)

        enumerator = make_enumerator(program)
        variants = list(enumerator.programs_enum)

        assert len(variants) == 0

    def test_no_variants_when_variable_already_final(self):
        """Final variable with assignment → no variants (already final)."""
        x = make_var_decl("x", is_final=True)
        assign = make_assignment("x")
        func = make_func("test", [x, assign])
        program = make_program(func)

        enumerator = make_enumerator(program)
        variants = list(enumerator.programs_enum)

        assert len(variants) == 0

    def test_one_variant_for_assigned_non_final_var(self):
        """Non-final variable with assignment → one variant."""
        x = make_var_decl("x", is_final=False)
        assign = make_assignment("x")
        func = make_func("test", [x, assign])
        program = make_program(func)

        enumerator = make_enumerator(program)
        variants = list(enumerator.programs_enum)

        assert len(variants) == 1

    def test_variant_has_final_flag_set(self):
        """During yield, the declaration's is_final flag is True."""
        x = make_var_decl("x", is_final=False)
        assign = make_assignment("x")
        func = make_func("test", [x, assign])
        program = make_program(func)

        enumerator = make_enumerator(program)
        # The enumerator deep-copies the program, so we check via _error_decl
        # which points to the actual (copied) declaration being mutated.
        yielded_states = []
        for _ in enumerator.programs_enum:
            yielded_states.append(enumerator._error_decl.is_final)

        assert yielded_states == [True]

    def test_is_final_restored_after_enumeration(self):
        """After enumeration completes, is_final is restored to False."""
        x = make_var_decl("x", is_final=False)
        assign = make_assignment("x")
        func = make_func("test", [x, assign])
        program = make_program(func)

        enumerator = make_enumerator(program)
        list(enumerator.programs_enum)

        # The enumerator works on a deep copy, so check via its internal state
        assert enumerator._error_decl is None

    def test_one_variant_for_assigned_non_final_field(self):
        """Non-final field with assignment in same class → one variant."""
        field = make_field_decl("f", is_final=False)
        assign = make_assignment("f")
        method = make_func("setF", [assign])
        cls = make_class("A", fields=[field], functions=[method])
        program = make_program(cls)

        enumerator = make_enumerator(program)
        variants = list(enumerator.programs_enum)

        assert len(variants) == 1

    def test_one_variant_for_field_assigned_in_subclass(self):
        """Non-final field assigned only in a subclass → one variant (hierarchy)."""
        field = make_field_decl("f", is_final=False)
        parent = make_class("Parent", fields=[field])

        assign = make_assignment("f")
        method = make_func("setF", [assign])
        child = make_class("Child",
                           superclasses=[make_superclass_inst(parent)],
                           functions=[method])
        program = make_program(parent, child)

        enumerator = make_enumerator(program)
        variants = list(enumerator.programs_enum)

        assert len(variants) == 1

    def test_no_variant_for_field_assigned_only_in_unrelated_class(self):
        """Field in class A not assigned in A or its subclasses → no variants."""
        field = make_field_decl("f", is_final=False)
        cls_a = make_class("A", fields=[field])

        assign = make_assignment("f")
        method = make_func("setF", [assign])
        cls_b = make_class("B", functions=[method])  # unrelated to A

        program = make_program(cls_a, cls_b)

        enumerator = make_enumerator(program)
        variants = list(enumerator.programs_enum)

        assert len(variants) == 0

    def test_multiple_candidates_yield_multiple_variants(self):
        """Two distinct assigned non-final variables → two variants."""
        x = make_var_decl("x", is_final=False)
        y = make_var_decl("y", is_final=False)
        ax = make_assignment("x")
        ay = make_assignment("y")
        func = make_func("test", [x, y, ax, ay])
        program = make_program(func)

        enumerator = make_enumerator(program)
        variants = list(enumerator.programs_enum)

        assert len(variants) == 2

    def test_unrelated_assignment_does_not_trigger_enumeration(self):
        """Non-final variable 'x' with assignment to unrelated name 'z' → no variants."""
        x = make_var_decl("x", is_final=False)
        az = make_assignment("z")
        func = make_func("test", [x, az])
        program = make_program(func)

        enumerator = make_enumerator(program)
        variants = list(enumerator.programs_enum)

        assert len(variants) == 0

    def test_error_explanation_set_during_yield(self):
        """error_explanation is populated while yielding."""
        x = make_var_decl("x", is_final=False)
        assign = make_assignment("x")
        func = make_func("test", [x, assign])
        program = make_program(func)

        enumerator = make_enumerator(program)
        explanations = []
        for _ in enumerator.programs_enum:
            explanations.append(enumerator.error_explanation)

        assert len(explanations) == 1
        assert "variable" in explanations[0]
        assert "x" in explanations[0]

    def test_error_explanation_none_after_enumeration(self):
        """After enumeration completes, error_explanation is None."""
        x = make_var_decl("x", is_final=False)
        assign = make_assignment("x")
        func = make_func("test", [x, assign])
        program = make_program(func)

        enumerator = make_enumerator(program)
        list(enumerator.programs_enum)

        assert enumerator.error_explanation is None


# ---------------------------------------------------------------------------
# 3. Metadata tests
# ---------------------------------------------------------------------------

class TestMetadata:

    def test_metadata_locations_counts_all_non_final_decls(self):
        """locations = total non-final vars + non-final fields."""
        x = make_var_decl("x", is_final=False)
        y = make_var_decl("y", is_final=True)   # final, not counted
        field = make_field_decl("f", is_final=False)
        func = make_func("test", [x, y])
        cls = make_class("A", fields=[field])
        program = make_program(func, cls)

        enumerator = make_enumerator(program)
        list(enumerator.programs_enum)

        # x (non-final var) + f (non-final field) = 2
        assert enumerator.metadata["locations"] == 2

    def test_metadata_examined_counts_only_assigned_candidates(self):
        """examined = non-final decls that actually have matching assignments."""
        x = make_var_decl("x", is_final=False)   # assigned → examined
        y = make_var_decl("y", is_final=False)   # not assigned → not examined
        ax = make_assignment("x")
        func = make_func("test", [x, y, ax])
        program = make_program(func)

        enumerator = make_enumerator(program)
        list(enumerator.programs_enum)

        assert enumerator.metadata["locations"] == 2
        assert enumerator.metadata["examined"] == 1
