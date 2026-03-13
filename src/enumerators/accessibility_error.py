from typing import NamedTuple, List, Dict, Optional

from src.enumerators.error import ErrorEnumerator
from src.ir import ast
from src.ir.visitors import DefaultVisitor
from src.ir.builtins import BuiltinFactory
from src.generators import Generator as ProgramGenerator


# Access modifier hierarchy: from most to least permissive.
# public <: protected <: private (using Liskov substitution principle:
# more accessible = subtype of less accessible).
ACCESS_MODIFIERS = ["public", "protected", "private"]


def _get_class_name(t) -> Optional[str]:
    """Extract a plain class name string from a type object."""
    if hasattr(t, 'name'):
        return t.name
    if hasattr(t, 't_constructor') and hasattr(t.t_constructor, 'name'):
        return t.t_constructor.name
    return None


class FuncLoc(NamedTuple):
    func_decl: ast.FunctionDeclaration
    class_decl: ast.ClassDeclaration


class AccessibilityAnalysis(DefaultVisitor):
    """
    Visitor that collects:
    1. All class method definitions and their enclosing class.
    2. All function call sites and their calling-class context.
    3. The class hierarchy (for subclass relationship checks).
    """

    def __init__(self):
        # Name of the class currently being visited (None at top level).
        self.current_class: Optional[str] = None
        # Map: class_name -> ClassDeclaration
        self.class_decls: Dict[str, ast.ClassDeclaration] = {}
        # Map: class_name -> list of direct superclass names
        self.superclasses: Dict[str, List[str]] = {}
        # Map: id(FunctionDeclaration) -> list of calling-class names
        #      (None means the call is at top level, outside any class)
        self.call_sites: Dict[int, List[Optional[str]]] = {}

    def result(self):
        pass

    # ------------------------------------------------------------------
    # Two-pass program traversal
    # ------------------------------------------------------------------

    def _register_class(self, node: ast.ClassDeclaration):
        """Register a class's name and superclasses without visiting its body."""
        self.class_decls[node.name] = node
        supers: List[str] = []
        for sc in node.superclasses:
            name = _get_class_name(sc.class_type)
            if name is not None:
                supers.append(name)
        self.superclasses[node.name] = supers

    def visit_program(self, node):
        """
        Override to do a two-pass traversal:
        Pass 1 – register every class (name, superclasses, method list) so
                 that forward references are resolved before any call-site
                 scanning begins.
        Pass 2 – visit each class declaration (including method bodies) to
                 collect call sites.
        """
        # Pass 1: register all classes without visiting bodies.
        for decl in node.declarations:
            if isinstance(decl, ast.ClassDeclaration):
                self._register_class(decl)

        # Pass 2: traverse bodies to find call sites.
        for decl in node.declarations:
            if isinstance(decl, ast.ClassDeclaration):
                self.visit_class_decl(decl)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _get_nest_host(self, class_name: str) -> str:
        """
        Return the outermost enclosing class (nest host) of *class_name*.

        Nested class names are formed by appending the simple name to the
        enclosing class name with a dot, e.g. ``pkg.Outer.Inner``.  We walk
        up the nesting by repeatedly stripping the last segment and checking
        whether the prefix is a known class.
        """
        candidate = class_name
        while True:
            dot_idx = candidate.rfind('.')
            if dot_idx == -1:
                break
            prefix = candidate[:dot_idx]
            if prefix in self.class_decls:
                candidate = prefix
            else:
                break
        return candidate

    def _find_method_in_hierarchy(
            self, class_name: str, method_name: str
    ) -> Optional[ast.FunctionDeclaration]:
        """
        Walk the inheritance chain of *class_name* (breadth-first) looking
        for a method named *method_name*.  Returns the first match found,
        or None if no match exists in the seed's known classes.
        """
        visited: set = set()
        queue: List[str] = [class_name]
        while queue:
            cname = queue.pop(0)
            if cname in visited:
                continue
            visited.add(cname)
            class_decl = self.class_decls.get(cname)
            if class_decl is not None:
                for func in class_decl.functions:
                    if func.name == method_name:
                        return func
            queue.extend(self.superclasses.get(cname, []))
        return None

    # ------------------------------------------------------------------
    # Visitor methods
    # ------------------------------------------------------------------

    def visit_class_decl(self, node: ast.ClassDeclaration):
        # Register the class (idempotent; a no-op when visit_program already
        # called _register_class in pass 1).
        self._register_class(node)
        prev_class = self.current_class
        self.current_class = node.name
        super().visit_class_decl(node)
        self.current_class = prev_class

    def _record_call_site(self, receiver, func_name: str):
        """
        Shared logic for visit_func_call and visit_func_ref: given a receiver
        expression and the referenced method name, record a call site entry if
        the method belongs to a known class.
        """
        if receiver is None:
            return
        type_info = receiver.get_type_info()
        if type_info is None:
            return
        _, actual_t = type_info
        class_name = _get_class_name(actual_t)
        if class_name is None:
            return
        func_decl = self._find_method_in_hierarchy(class_name, func_name)
        if func_decl is not None:
            self.call_sites.setdefault(id(func_decl), []).append(
                self.current_class
            )

    def visit_func_call(self, node: ast.FunctionCall):
        super().visit_func_call(node)
        self._record_call_site(node.receiver, node.func)

    def visit_func_ref(self, node: ast.FunctionReference):
        super().visit_func_ref(node)
        self._record_call_site(node.receiver, node.func)


class AccessibilityErrorEnumerator(ErrorEnumerator):
    """
    Injects accessibility violations by tightening a method's access modifier
    to a value that is guaranteed to make at least one existing call site
    ill-typed.

    The access modifier hierarchy (most → least permissive):
        public  →  protected  →  private

    Soundness conditions for each injected modifier:
    * ``protected``: sound when there is at least one call from a class that
      is *not* the defining class and *not* a subclass of it.
    * ``private``: sound when there is at least one call from any class other
      than the defining class (including subclasses).
    """

    name = "AccessibilityErrorEnumerator"
    ACCESS_MODIFIERS = ACCESS_MODIFIERS

    def __init__(self, program: ast.Program,
                 program_gen: ProgramGenerator,
                 bt_factory: BuiltinFactory,
                 options: dict = None):
        self.analysis = AccessibilityAnalysis()
        self.options = options or {}
        self.error_func_decl: Optional[ast.FunctionDeclaration] = None
        self.error_class_decl: Optional[ast.ClassDeclaration] = None
        self.original_access_mod: Optional[str] = None
        self.injected_access_mod: Optional[str] = None
        self.metadata: Dict[str, int] = {
            "locations": 0,
            "examined": 0,
        }
        super().__init__(program, program_gen, bt_factory)

    # ------------------------------------------------------------------
    # Error explanation (mirrors TypeErrorEnumerator.error_explanation)
    # ------------------------------------------------------------------

    @property
    def error_explanation(self) -> Optional[str]:
        if self.error_func_decl is None:
            return None
        return (
            f"Added accessibility error using {self.name}:\n"
            f" - Method: "
            f"{self.error_class_decl.name}.{self.error_func_decl.name}\n"
            f" - Original access modifier: {self.original_access_mod}\n"
            f" - Injected access modifier: {self.injected_access_mod}\n"
        )

    # ------------------------------------------------------------------
    # ErrorEnumerator interface
    # ------------------------------------------------------------------

    def add_err_message(self, loc: FuncLoc, new_access_mod, *args):
        """Record which function was mutated and how."""
        self.error_func_decl = loc.func_decl
        self.error_class_decl = loc.class_decl
        self.injected_access_mod = new_access_mod

    def get_candidate_program_locations(self) -> List[FuncLoc]:
        """Return every class method in the seed as a candidate location."""
        self.analysis.visit_program(self.program)
        locations: List[FuncLoc] = []
        for class_decl in self.analysis.class_decls.values():
            for func in class_decl.functions:
                if func.func_type == ast.FunctionDeclaration.CLASS_METHOD:
                    locations.append(FuncLoc(func_decl=func,
                                             class_decl=class_decl))
        return locations

    def filter_program_locations(
            self, locations: List[FuncLoc]
    ) -> List[FuncLoc]:
        """
        Keep only locations where it is sound to inject an accessibility error,
        i.e. the method has at least one call site from outside its defining
        class.
        """
        self.metadata["locations"] = len(locations)
        filtered: List[FuncLoc] = []

        for loc in locations:
            func_decl = loc.func_decl
            class_decl = loc.class_decl

            # Cannot tighten already-private methods.
            access_mod = func_decl.metadata.get("access_mod", "public")
            if access_mod == "private":
                continue

            # Abstract methods (no body) cannot be given a tighter modifier
            # in a sound way because they are overridden in subclasses.
            if func_decl.body is None:
                continue

            func_id = id(func_decl)
            all_calls = self.analysis.call_sites.get(func_id, [])
            if not all_calls:
                continue  # Never called – no error can be guaranteed.

            # Calls from outside the defining class's nest are the only ones
            # that can guarantee an accessibility error (nest members have
            # unrestricted access to private/protected members in Java 11+).
            external_calls = [
                c for c in all_calls
                if not self._are_nest_members(c, class_decl.name)
            ]
            if not external_calls:
                continue

            filtered.append(loc)

        self.metadata["examined"] = len(filtered)
        return filtered

    def get_programs_with_error(self, loc: FuncLoc):
        """
        Yield program variants where the method's access modifier has been
        changed to a more restrictive value, in order of decreasing
        permissiveness (public → protected → private).
        """
        func_decl = loc.func_decl
        class_decl = loc.class_decl
        original_access_mod = func_decl.metadata.get("access_mod", "public")
        self.original_access_mod = original_access_mod

        func_id = id(func_decl)
        all_calls = self.analysis.call_sites.get(func_id, [])

        # Calls from outside the defining class's nest (nest members have
        # unrestricted access to private/protected members in Java 11+).
        external_calls = [
            c for c in all_calls
            if not self._are_nest_members(c, class_decl.name)
        ]

        # Subset of external calls that are also not from subclasses
        # (subclasses can always access protected members).
        outside_calls = [
            c for c in external_calls
            if not self._is_subclass_of(c, class_decl.name)
        ]

        current_idx = (
            self.ACCESS_MODIFIERS.index(original_access_mod)
            if original_access_mod in self.ACCESS_MODIFIERS
            else 0
        )

        try:
            for new_mod in self.ACCESS_MODIFIERS[current_idx + 1:]:
                # Soundness check ------------------------------------------------
                # protected: accessible from the defining class and its subclasses.
                # An error is guaranteed only if there is a call from a class
                # that is neither the defining class nor one of its subclasses.
                if new_mod == "protected" and not outside_calls:
                    continue

                # private: accessible only from the defining class.
                # An error is guaranteed whenever there is any external call.
                if new_mod == "private" and not external_calls:
                    continue
                # ----------------------------------------------------------------

                func_decl.metadata["access_mod"] = new_mod
                self.add_err_message(loc, new_mod)
                yield self.program

        finally:
            # Always restore the original access modifier so subsequent
            # locations see an unmodified program.
            func_decl.metadata["access_mod"] = original_access_mod

    def enumerate_programs(self):
        """
        Override the base-class implementation because our locations are
        function declarations (not expressions) and do not need the
        ASTExprUpdate restoration mechanism used by ErrorEnumerator.
        """
        locations = self.get_candidate_program_locations()
        locations = self.filter_program_locations(locations)
        for loc in locations:
            yield from self.get_programs_with_error(loc)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _are_nest_members(self, c1: Optional[str], c2: str) -> bool:
        """
        Return True if *c1* and *c2* belong to the same nest (i.e. share the
        same top-level enclosing class / nest host).

        In Java 11+ (JEP 181) nest members have unrestricted access to each
        other's ``private`` members.  Nested classes are also in the same
        package as their enclosing class, so they always have access to
        ``protected`` members as well.

        None (top-level / global scope) is never a nest member of any class.
        """
        if c1 is None:
            return False
        return (self.analysis._get_nest_host(c1) ==
                self.analysis._get_nest_host(c2))

    def _is_subclass_of(self, child: Optional[str], parent: str) -> bool:
        """
        Return True if *child* is the same as *parent* or transitively
        inherits from it, according to the class hierarchy collected by
        AccessibilityAnalysis.

        None (top-level / global scope) is never a subclass of anything.
        """
        if child is None:
            return False
        visited: set = set()
        queue: List[Optional[str]] = [child]
        while queue:
            curr = queue.pop(0)
            if curr in visited or curr is None:
                continue
            if curr == parent:
                return True
            visited.add(curr)
            queue.extend(self.analysis.superclasses.get(curr, []))
        return False
