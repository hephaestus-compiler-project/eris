from typing import Dict, List, Optional, Set, Tuple

from src.ir import ast
from src.ir.builtins import BuiltinFactory
from src.ir.visitors import DefaultVisitor
from src.generators import Generator
from src.enumerators.error import ErrorEnumerator


def _get_class_name(t) -> Optional[str]:
    """Extract a plain class name string from a type object."""
    if hasattr(t, 'name'):
        return t.name
    if hasattr(t, 't_constructor') and hasattr(t.t_constructor, 'name'):
        return t.t_constructor.name
    return None


class FinalVarAnalysis(DefaultVisitor):
    """
    Visitor that collects:
      - All non-final VariableDeclaration nodes in the program.
      - All non-final FieldDeclaration nodes paired with their declaring class.
      - Assignment target names per class (for hierarchy-aware field matching).
      - The class hierarchy (superclass relationships).
    """

    def __init__(self):
        self.var_decls: List[ast.VariableDeclaration] = []
        # Each entry is (field_decl, declaring_class_name)
        self.field_decls: List[Tuple[ast.FieldDeclaration, str]] = []
        self.superclasses: Dict[str, List[str]] = {}
        # All assignment names in the program (for local variable matching)
        self.all_assigned_names: Set[str] = set()
        # Per-class assignment names (for hierarchy-aware field matching)
        self.class_assignments: Dict[str, Set[str]] = {}
        self._current_class: Optional[str] = None

    def result(self):
        pass

    def visit_program(self, node: ast.Program):
        # Two-pass: register class hierarchy first, then visit bodies.
        for decl in node.declarations:
            if isinstance(decl, ast.ClassDeclaration):
                self._register_class(decl)
        super().visit_program(node)

    def _register_class(self, node: ast.ClassDeclaration):
        supers: List[str] = []
        for sc in node.superclasses:
            name = _get_class_name(sc.class_type)
            if name is not None:
                supers.append(name)
        self.superclasses[node.name] = supers
        self.class_assignments.setdefault(node.name, set())

    def visit_class_decl(self, node: ast.ClassDeclaration):
        prev = self._current_class
        self._current_class = node.name
        super().visit_class_decl(node)
        self._current_class = prev

    def visit_var_decl(self, node: ast.VariableDeclaration):
        if not node.is_final:
            self.var_decls.append(node)
        super().visit_var_decl(node)

    def visit_field_decl(self, node: ast.FieldDeclaration):
        if not node.is_final and self._current_class is not None:
            self.field_decls.append((node, self._current_class))
        super().visit_field_decl(node)

    def visit_assign(self, node: ast.Assignment):
        self.all_assigned_names.add(node.name)
        if self._current_class is not None:
            self.class_assignments.setdefault(
                self._current_class, set()).add(node.name)
        super().visit_assign(node)

    def is_field_assigned(self, field_name: str, class_name: str) -> bool:
        """Return True if field_name is assigned in class_name or any subclass."""
        for cls, assigned in self.class_assignments.items():
            if field_name in assigned and self._is_subclass_or_equal(cls, class_name):
                return True
        return False

    def _is_subclass_or_equal(self, child: str, parent: str) -> bool:
        visited: Set[str] = set()
        queue = [child]
        while queue:
            curr = queue.pop(0)
            if curr in visited:
                continue
            if curr == parent:
                return True
            visited.add(curr)
            queue.extend(self.superclasses.get(curr, []))
        return False


class FinalVarErrorEnumerator(ErrorEnumerator):
    """
    Enumerator that injects errors by converting non-final variable and field
    declarations into final ones, making any existing assignments to them
    invalid.

    For every VariableDeclaration or FieldDeclaration that:
      (a) is not final, and
      (b) has at least one Assignment targeting its name,
    the enumerator temporarily marks the declaration as final and yields the
    program as an ill-typed variant.

    For fields, the assignment check is hierarchy-aware: an assignment in a
    subclass counts as an assignment to the superclass's field.
    """

    name = "FinalVarErrorEnumerator"

    def __init__(self, program: ast.Program, program_gen: Generator,
                 bt_factory: BuiltinFactory, options: dict = None):
        self._error_decl: Optional[ast.Declaration] = None
        self._error_decl_kind: Optional[str] = None
        self.metadata: dict = {
            "locations": 0,
            "examined": 0,
        }
        super().__init__(program, program_gen, bt_factory)

    # ------------------------------------------------------------------
    # Abstract method stubs (enumeration is driven by enumerate_programs)
    # ------------------------------------------------------------------

    def get_candidate_program_locations(self):
        return []

    def filter_program_locations(self, locations):
        return locations

    def get_programs_with_error(self, location):
        return iter([])

    def add_err_message(self, loc, new_node, *args):
        pass

    # ------------------------------------------------------------------
    # Error description
    # ------------------------------------------------------------------

    @property
    def error_explanation(self) -> Optional[str]:
        if self._error_decl is None:
            return None
        return (
            f"Added final assignment error using {self.name}:\n"
            f" - {self._error_decl_kind}: {self._error_decl.name}\n"
            f" - Converted to final; assignments become invalid\n"
        )

    # ------------------------------------------------------------------
    # Core enumeration
    # ------------------------------------------------------------------

    def enumerate_programs(self):
        analysis = FinalVarAnalysis()
        analysis.visit(self.program)

        all_decls: List[Tuple[ast.Declaration, str]] = (
            [(decl, "variable") for decl in analysis.var_decls] +
            [(decl, "field") for decl, _ in analysis.field_decls]
        )
        self.metadata["locations"] = len(all_decls)

        candidates: List[Tuple[ast.Declaration, str]] = (
            [
                (decl, "variable") for decl in analysis.var_decls
                if decl.name in analysis.all_assigned_names
            ] + [
                (decl, "field")
                for decl, class_name in analysis.field_decls
                if analysis.is_field_assigned(decl.name, class_name)
            ]
        )
        self.metadata["examined"] = len(candidates)

        for decl, kind in candidates:
            decl.is_final = True
            self._error_decl = decl
            self._error_decl_kind = kind
            yield self.program
            decl.is_final = False

        self._error_decl = None
        self._error_decl_kind = None
