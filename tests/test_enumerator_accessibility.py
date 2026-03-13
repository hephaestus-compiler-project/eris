"""
Unit tests for the AccessibilityErrorEnumerator.

Tests are organised into three groups:
  1. AccessibilityAnalysis – the visitor that collects call sites.
  2. _is_subclass_of      – the subclass-relationship helper.
  3. Enumeration logic    – filter_program_locations / get_programs_with_error.
"""
import pytest

from src.ir import ast, types as tp, java_types as jt
from src.ir.context import Context
from src.enumerators.accessibility_error import (
    AccessibilityAnalysis,
    AccessibilityErrorEnumerator,
    FuncLoc,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_void_block() -> ast.Block:
    return ast.Block([])


def make_method(name: str, access_mod: str = "public",
                body=None) -> ast.FunctionDeclaration:
    """Create a minimal class method with the given access modifier."""
    return ast.FunctionDeclaration(
        name=name,
        params=[],
        ret_type=jt.Void,
        body=body if body is not None else make_void_block(),
        func_type=ast.FunctionDeclaration.CLASS_METHOD,
        metadata={"access_mod": access_mod},
    )


def make_call(method_name: str, receiver_type: tp.Type) -> ast.FunctionCall:
    """Create a FunctionCall node whose receiver has the given type."""
    receiver = ast.Variable("obj")
    receiver.mk_typed(ast.TypePair(expected=receiver_type,
                                   actual=receiver_type))
    return ast.FunctionCall(func=method_name, args=[], receiver=receiver)


def make_class(name: str, methods=None,
               superclasses=None) -> ast.ClassDeclaration:
    """Create a ClassDeclaration."""
    return ast.ClassDeclaration(
        name=name,
        superclasses=superclasses or [],
        functions=methods or [],
    )


def make_program(*class_decls: ast.ClassDeclaration) -> ast.Program:
    """Wrap class declarations in a minimal Program."""
    ctx = Context()
    program = ast.Program(ctx, "java")
    for cls in class_decls:
        program.add_declaration(cls)
    return program


def make_enumerator(program: ast.Program) -> AccessibilityErrorEnumerator:
    """Instantiate the enumerator; program_gen is not needed for our tests."""
    return AccessibilityErrorEnumerator(
        program=program,
        program_gen=None,
        bt_factory=jt.JavaBuiltinFactory(),
        options={},
    )


# ---------------------------------------------------------------------------
# 1. AccessibilityAnalysis tests
# ---------------------------------------------------------------------------

class TestAccessibilityAnalysis:

    def test_no_calls_registered_when_no_function_calls(self):
        """A method with an empty body produces no call-site entries."""
        foo = make_method("foo")
        class_a = make_class("A", methods=[foo])

        analysis = AccessibilityAnalysis()
        analysis.visit_class_decl(class_a)

        assert analysis.call_sites == {}

    def test_call_from_different_class_is_recorded(self):
        """A call to A.foo() from inside B.bar() is recorded with caller='B'."""
        type_a = tp.SimpleClassifier("A", [])
        foo = make_method("foo")
        class_a = make_class("A", methods=[foo])

        call = make_call("foo", type_a)
        bar = make_method("bar", body=ast.Block([call]))
        class_b = make_class("B", methods=[bar])

        analysis = AccessibilityAnalysis()
        analysis.visit_class_decl(class_a)
        analysis.visit_class_decl(class_b)

        callers = analysis.call_sites.get(id(foo), [])
        assert callers == ["B"]

    def test_call_from_same_class_is_recorded(self):
        """A call to A.foo() from inside A.bar() is recorded with caller='A'."""
        type_a = tp.SimpleClassifier("A", [])
        foo = make_method("foo")
        call = make_call("foo", type_a)
        bar = make_method("bar", body=ast.Block([call]))
        class_a = make_class("A", methods=[foo, bar])

        analysis = AccessibilityAnalysis()
        analysis.visit_class_decl(class_a)

        callers = analysis.call_sites.get(id(foo), [])
        assert callers == ["A"]

    def test_multiple_callers_are_all_recorded(self):
        """Both class B and class C calling A.foo() are recorded."""
        type_a = tp.SimpleClassifier("A", [])
        foo = make_method("foo")
        class_a = make_class("A", methods=[foo])

        call_b = make_call("foo", type_a)
        class_b = make_class("B", methods=[make_method("bar", body=ast.Block([call_b]))])

        call_c = make_call("foo", type_a)
        class_c = make_class("C", methods=[make_method("baz", body=ast.Block([call_c]))])

        analysis = AccessibilityAnalysis()
        analysis.visit_class_decl(class_a)
        analysis.visit_class_decl(class_b)
        analysis.visit_class_decl(class_c)

        callers = analysis.call_sites.get(id(foo), [])
        assert set(callers) == {"B", "C"}

    def test_method_resolution_through_inheritance(self):
        """A call on a receiver of type B finds foo() defined in superclass A."""
        type_a = tp.SimpleClassifier("A", [])
        type_b = tp.SimpleClassifier("B", [type_a])

        foo = make_method("foo")
        class_a = make_class("A", methods=[foo])

        sc = ast.SuperClassInstantiation(class_type=type_a)
        # B has no own foo; call should resolve to A.foo
        call = make_call("foo", type_b)
        class_b = make_class("B",
                              methods=[make_method("bar", body=ast.Block([call]))],
                              superclasses=[sc])

        analysis = AccessibilityAnalysis()
        analysis.visit_class_decl(class_a)
        analysis.visit_class_decl(class_b)

        callers = analysis.call_sites.get(id(foo), [])
        assert callers == ["B"]

    def test_calls_without_receiver_are_ignored(self):
        """FunctionCall nodes with no receiver (e.g. free functions) do not
        produce call-site entries for class methods."""
        foo = make_method("foo")
        class_a = make_class("A", methods=[foo])

        # A call with no receiver
        bare_call = ast.FunctionCall(func="foo", args=[], receiver=None)
        class_b = make_class("B", methods=[make_method("bar",
                                                        body=ast.Block([bare_call]))])

        analysis = AccessibilityAnalysis()
        analysis.visit_class_decl(class_a)
        analysis.visit_class_decl(class_b)

        assert analysis.call_sites.get(id(foo), []) == []

    def test_superclass_hierarchy_collected(self):
        """Superclass relationships are stored in analysis.superclasses."""
        type_a = tp.SimpleClassifier("A", [])
        sc = ast.SuperClassInstantiation(class_type=type_a)
        class_a = make_class("A")
        class_b = make_class("B", superclasses=[sc])

        analysis = AccessibilityAnalysis()
        analysis.visit_class_decl(class_a)
        analysis.visit_class_decl(class_b)

        assert analysis.superclasses["A"] == []
        assert "A" in analysis.superclasses["B"]


# ---------------------------------------------------------------------------
# 2. _is_subclass_of tests
# ---------------------------------------------------------------------------

class TestIsSubclassOf:

    def _make_enum_with_hierarchy(self, hierarchy: dict):
        """
        Build an enumerator whose analysis has the given superclass map.
        hierarchy: {class_name: [list of direct superclass names]}
        """
        enum = object.__new__(AccessibilityErrorEnumerator)
        enum.analysis = AccessibilityAnalysis()
        enum.analysis.superclasses = hierarchy
        return enum

    def test_same_class_is_subclass_of_itself(self):
        enum = self._make_enum_with_hierarchy({"A": []})
        assert enum._is_subclass_of("A", "A")

    def test_direct_subclass(self):
        enum = self._make_enum_with_hierarchy({"A": [], "B": ["A"]})
        assert enum._is_subclass_of("B", "A")
        assert not enum._is_subclass_of("A", "B")

    def test_transitive_subclass(self):
        enum = self._make_enum_with_hierarchy(
            {"A": [], "B": ["A"], "C": ["B"]}
        )
        assert enum._is_subclass_of("C", "A")
        assert not enum._is_subclass_of("A", "C")

    def test_unrelated_classes(self):
        enum = self._make_enum_with_hierarchy({"A": [], "B": []})
        assert not enum._is_subclass_of("A", "B")
        assert not enum._is_subclass_of("B", "A")

    def test_none_is_never_a_subclass(self):
        """None represents the top-level (global) scope, never a subclass."""
        enum = self._make_enum_with_hierarchy({"A": []})
        assert not enum._is_subclass_of(None, "A")


# ---------------------------------------------------------------------------
# 3. Enumeration logic tests
# ---------------------------------------------------------------------------

class TestEnumerationLogic:

    # -- filter_program_locations ------------------------------------------

    def test_filter_skips_already_private_methods(self):
        """Methods already marked private cannot be made more restrictive."""
        foo = make_method("foo", access_mod="private")
        type_a = tp.SimpleClassifier("A", [])
        # Simulate a call from B so the method would otherwise be eligible
        call = make_call("foo", type_a)
        bar = make_method("bar", body=ast.Block([call]))
        class_a = make_class("A", methods=[foo])
        class_b = make_class("B", methods=[bar])

        enum = make_enumerator(make_program(class_a, class_b))
        locs = enum.get_candidate_program_locations()
        filtered = enum.filter_program_locations(locs)

        # foo should not appear (private); bar is in B, unrelated
        method_names = {loc.func_decl.name for loc in filtered}
        assert "foo" not in method_names

    def test_filter_skips_abstract_methods(self):
        """Abstract methods (body is None) are excluded."""
        foo = ast.FunctionDeclaration(
            name="foo", params=[], ret_type=jt.Void, body=None,
            func_type=ast.FunctionDeclaration.CLASS_METHOD,
            metadata={"access_mod": "public"},
        )
        type_a = tp.SimpleClassifier("A", [])
        call = make_call("foo", type_a)
        class_a = make_class("A", methods=[foo])
        class_b = make_class("B",
                              methods=[make_method("bar", body=ast.Block([call]))])

        enum = make_enumerator(make_program(class_a, class_b))
        locs = enum.get_candidate_program_locations()
        filtered = enum.filter_program_locations(locs)

        method_names = {loc.func_decl.name for loc in filtered}
        assert "foo" not in method_names

    def test_filter_skips_methods_with_no_calls(self):
        """Methods that are never called cannot produce a guaranteed error."""
        foo = make_method("foo")
        class_a = make_class("A", methods=[foo])

        enum = make_enumerator(make_program(class_a))
        locs = enum.get_candidate_program_locations()
        filtered = enum.filter_program_locations(locs)

        assert filtered == []

    def test_filter_skips_methods_called_only_within_own_class(self):
        """Calls exclusively from inside the defining class cannot be
        turned into access violations."""
        type_a = tp.SimpleClassifier("A", [])
        foo = make_method("foo")
        call = make_call("foo", type_a)
        bar = make_method("bar", body=ast.Block([call]))
        class_a = make_class("A", methods=[foo, bar])

        enum = make_enumerator(make_program(class_a))
        locs = enum.get_candidate_program_locations()
        filtered = enum.filter_program_locations(locs)

        method_names = {loc.func_decl.name for loc in filtered}
        assert "foo" not in method_names

    def test_filter_keeps_method_called_from_external_class(self):
        """A public method called from an unrelated class should survive
        filtering."""
        type_a = tp.SimpleClassifier("A", [])
        foo = make_method("foo")
        call = make_call("foo", type_a)
        class_a = make_class("A", methods=[foo])
        class_b = make_class("B",
                              methods=[make_method("bar", body=ast.Block([call]))])

        enum = make_enumerator(make_program(class_a, class_b))
        locs = enum.get_candidate_program_locations()
        filtered = enum.filter_program_locations(locs)

        method_names = {loc.func_decl.name for loc in filtered}
        assert "foo" in method_names

    def test_filter_keeps_method_called_from_subclass(self):
        """A public method called only from a subclass should still pass
        filtering (private injection is sound)."""
        type_a = tp.SimpleClassifier("A", [])
        type_b = tp.SimpleClassifier("B", [type_a])

        foo = make_method("foo")
        sc = ast.SuperClassInstantiation(class_type=type_a)
        call = make_call("foo", type_b)
        class_a = make_class("A", methods=[foo])
        class_b = make_class("B",
                              methods=[make_method("bar", body=ast.Block([call]))],
                              superclasses=[sc])

        enum = make_enumerator(make_program(class_a, class_b))
        locs = enum.get_candidate_program_locations()
        filtered = enum.filter_program_locations(locs)

        method_names = {loc.func_decl.name for loc in filtered}
        assert "foo" in method_names

    # -- get_programs_with_error / full enumeration -------------------------

    def test_public_method_called_from_unrelated_class_yields_two_variants(self):
        """
        public A.foo() called from unrelated B should generate:
          1) protected variant  (sound: B is not a subclass)
          2) private variant    (sound: B is external)
        """
        type_a = tp.SimpleClassifier("A", [])
        foo = make_method("foo", access_mod="public")
        call = make_call("foo", type_a)
        class_a = make_class("A", methods=[foo])
        class_b = make_class("B",
                              methods=[make_method("bar", body=ast.Block([call]))])

        enum = make_enumerator(make_program(class_a, class_b))
        programs = list(enum.enumerate_programs())

        # Collect the access modifiers actually injected
        injected = [p for p in programs]
        # We check via the enumerator's error_explanation which records them
        assert len(programs) == 2

    def test_public_method_injected_access_mods_unrelated_class(self):
        """
        Verify that the injected modifiers are exactly 'protected' then
        'private' when the caller is unrelated.
        """
        type_a = tp.SimpleClassifier("A", [])
        foo = make_method("foo", access_mod="public")
        call = make_call("foo", type_a)
        class_a = make_class("A", methods=[foo])
        class_b = make_class("B",
                              methods=[make_method("bar", body=ast.Block([call]))])

        enum = make_enumerator(make_program(class_a, class_b))
        injected_mods = []
        for _ in enum.enumerate_programs():
            injected_mods.append(enum.injected_access_mod)

        assert injected_mods == ["protected", "private"]

    def test_public_method_called_only_from_subclass_yields_only_private(self):
        """
        public A.foo() called only from subclass B:
          - protected would still allow B to call foo => NOT sound => skipped
          - private prevents B from calling foo => sound => generated
        """
        type_a = tp.SimpleClassifier("A", [])
        type_b = tp.SimpleClassifier("B", [type_a])

        foo = make_method("foo", access_mod="public")
        sc = ast.SuperClassInstantiation(class_type=type_a)
        # Receiver is type B (a subclass) but method resolves to A.foo
        call = make_call("foo", type_b)
        class_a = make_class("A", methods=[foo])
        class_b = make_class("B",
                              methods=[make_method("bar", body=ast.Block([call]))],
                              superclasses=[sc])

        enum = make_enumerator(make_program(class_a, class_b))
        injected_mods = []
        for _ in enum.enumerate_programs():
            injected_mods.append(enum.injected_access_mod)

        assert injected_mods == ["private"]

    def test_protected_method_called_from_unrelated_class_yields_private(self):
        """
        protected A.foo() called from unrelated C:
          - cannot go to 'protected' again (already there)
          - 'private' is sound
        """
        type_a = tp.SimpleClassifier("A", [])
        foo = make_method("foo", access_mod="protected")
        call = make_call("foo", type_a)
        class_a = make_class("A", methods=[foo])
        class_c = make_class("C",
                              methods=[make_method("baz", body=ast.Block([call]))])

        enum = make_enumerator(make_program(class_a, class_c))
        injected_mods = []
        for _ in enum.enumerate_programs():
            injected_mods.append(enum.injected_access_mod)

        assert injected_mods == ["private"]

    def test_access_modifier_is_restored_after_enumeration(self):
        """
        After iterating all variants the original access modifier must be
        restored so the program remains well-typed.
        """
        type_a = tp.SimpleClassifier("A", [])
        foo = make_method("foo", access_mod="public")
        call = make_call("foo", type_a)
        class_a = make_class("A", methods=[foo])
        class_b = make_class("B",
                              methods=[make_method("bar", body=ast.Block([call]))])

        enum = make_enumerator(make_program(class_a, class_b))

        # Exhaust all variants
        list(enum.enumerate_programs())

        # Find the foo declaration inside the (deepcopied) program
        cls_a_decl = next(
            d for d in enum.program.declarations
            if isinstance(d, ast.ClassDeclaration) and d.name == "A"
        )
        foo_in_program = next(f for f in cls_a_decl.functions
                              if f.name == "foo")
        assert foo_in_program.metadata["access_mod"] == "public"

    def test_no_variants_for_method_never_called(self):
        """A method with no call sites produces zero variants."""
        foo = make_method("foo", access_mod="public")
        class_a = make_class("A", methods=[foo])

        enum = make_enumerator(make_program(class_a))
        assert list(enum.enumerate_programs()) == []

    def test_no_variants_for_method_called_only_within_own_class(self):
        """Calls exclusively from the defining class produce zero variants."""
        type_a = tp.SimpleClassifier("A", [])
        foo = make_method("foo", access_mod="public")
        call = make_call("foo", type_a)
        bar = make_method("bar", body=ast.Block([call]))
        class_a = make_class("A", methods=[foo, bar])

        enum = make_enumerator(make_program(class_a))
        assert list(enum.enumerate_programs()) == []


# ---------------------------------------------------------------------------
# 4. Nest-member tests
# ---------------------------------------------------------------------------

def make_func_ref(method_name: str, receiver_type: tp.Type) -> ast.FunctionReference:
    """Create a FunctionReference node whose receiver has the given type."""
    receiver = ast.Variable("obj")
    receiver.mk_typed(ast.TypePair(expected=receiver_type, actual=receiver_type))
    return ast.FunctionReference(
        func=method_name,
        receiver=receiver,
        signature=jt.Void,
        function_type=None,
    )


class TestNestMembers:

    def _make_enum(self, class_decls):
        """Build enumerator with the given class declarations pre-registered."""
        enum = object.__new__(AccessibilityErrorEnumerator)
        enum.analysis = AccessibilityAnalysis()
        for cd in class_decls:
            enum.analysis.class_decls[cd.name] = cd
            enum.analysis.superclasses[cd.name] = []
        return enum

    def test_nest_host_top_level_class(self):
        """A class with no enclosing class is its own nest host."""
        analysis = AccessibilityAnalysis()
        analysis.class_decls["pkg.Outer"] = make_class("pkg.Outer")
        assert analysis._get_nest_host("pkg.Outer") == "pkg.Outer"

    def test_nest_host_one_level_nested(self):
        """A one-level nested class has the outer class as its nest host."""
        analysis = AccessibilityAnalysis()
        analysis.class_decls["pkg.Outer"] = make_class("pkg.Outer")
        analysis.class_decls["pkg.Outer.Inner"] = make_class("pkg.Outer.Inner")
        assert analysis._get_nest_host("pkg.Outer.Inner") == "pkg.Outer"

    def test_nest_host_two_levels_nested(self):
        """A doubly-nested class has the outermost class as its nest host."""
        analysis = AccessibilityAnalysis()
        analysis.class_decls["pkg.Outer"] = make_class("pkg.Outer")
        analysis.class_decls["pkg.Outer.Mid"] = make_class("pkg.Outer.Mid")
        analysis.class_decls["pkg.Outer.Mid.Deep"] = make_class("pkg.Outer.Mid.Deep")
        assert analysis._get_nest_host("pkg.Outer.Mid.Deep") == "pkg.Outer"

    def test_are_nest_members_outer_and_inner(self):
        """Outer and its direct nested class are nest members."""
        enum = self._make_enum([
            make_class("pkg.Outer"),
            make_class("pkg.Outer.Inner"),
        ])
        assert enum._are_nest_members("pkg.Outer.Inner", "pkg.Outer")
        assert enum._are_nest_members("pkg.Outer", "pkg.Outer.Inner")

    def test_are_nest_members_two_siblings(self):
        """Two nested classes of the same outer class are nest members."""
        enum = self._make_enum([
            make_class("pkg.Outer"),
            make_class("pkg.Outer.A"),
            make_class("pkg.Outer.B"),
        ])
        assert enum._are_nest_members("pkg.Outer.A", "pkg.Outer.B")

    def test_not_nest_members_different_top_level(self):
        """Classes with different top-level parents are not nest members."""
        enum = self._make_enum([
            make_class("pkg.Alpha"),
            make_class("pkg.Beta"),
        ])
        assert not enum._are_nest_members("pkg.Alpha", "pkg.Beta")

    def test_none_is_not_a_nest_member(self):
        """None (global scope) is never a nest member of any class."""
        enum = self._make_enum([make_class("pkg.Outer")])
        assert not enum._are_nest_members(None, "pkg.Outer")

    def test_nested_class_call_not_external_for_private(self):
        """
        A call from a nested class (Outer.Inner) to a method of Outer should
        NOT count as an external call: private injection would be unsound
        because nest members can access private members in Java 11+.
        """
        type_outer = tp.SimpleClassifier("Outer", [])
        foo = make_method("foo", access_mod="public")
        call = make_call("foo", type_outer)
        class_outer = make_class("Outer", methods=[foo])
        class_inner = make_class("Outer.Inner",
                                 methods=[make_method("bar",
                                                      body=ast.Block([call]))])

        enum = make_enumerator(make_program(class_outer, class_inner))
        # No variants should be produced: the only caller is a nest member.
        assert list(enum.enumerate_programs()) == []

    def test_function_reference_recorded_as_call_site(self):
        """
        A method reference (obj::methodName) should be recorded the same way
        as a regular function call.
        """
        type_a = tp.SimpleClassifier("A", [])
        foo = make_method("foo")
        func_ref = make_func_ref("foo", type_a)
        bar = make_method("bar", body=ast.Block([func_ref]))
        class_a = make_class("A", methods=[foo])
        class_b = make_class("B", methods=[bar])

        analysis = AccessibilityAnalysis()
        analysis.visit_program(make_program(class_a, class_b))

        callers = analysis.call_sites.get(id(foo), [])
        assert callers == ["B"]

    def test_function_reference_enables_error_injection(self):
        """
        When the only use of a method is via a method reference from an
        external class, the enumerator should still produce variants.
        """
        type_a = tp.SimpleClassifier("A", [])
        foo = make_method("foo", access_mod="public")
        func_ref = make_func_ref("foo", type_a)
        bar = make_method("bar", body=ast.Block([func_ref]))
        class_a = make_class("A", methods=[foo])
        class_b = make_class("B", methods=[bar])

        enum = make_enumerator(make_program(class_a, class_b))
        injected_mods = []
        for _ in enum.enumerate_programs():
            injected_mods.append(enum.injected_access_mod)

        assert injected_mods == ["protected", "private"]
