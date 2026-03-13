from typing import NamedTuple, List, Dict, Optional, Union

from src.enumerators.error import ErrorEnumerator
from src.ir import ast
from src.ir.visitors import DefaultVisitor
from src.ir.builtins import BuiltinFactory
from src.generators import Generator as ProgramGenerator


# Access modifier hierarchy: from most to least permissive.
# public <: protected <: private (using Liskov substitution principle:
# more accessible = subtype of less accessible).
ACCESS_MODIFIERS = ["public", "protected", "private"]

# Sentinel used by FunctionReference to indicate a constructor reference.
_NEW_REF = ast.FunctionReference.NEW_REF


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


class CtorLoc(NamedTuple):
    ctor: ast.Constructor
    class_decl: ast.ClassDeclaration


# A location is either a method or a constructor.
Loc = Union[FuncLoc, CtorLoc]


class AccessibilityAnalysis(DefaultVisitor):
    """
    Visitor that collects:
    1. All class method/constructor definitions and their enclosing class.
    2. All call sites (function calls, function references, constructor calls)
       and their calling-class context.
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
        # Map: id(Constructor) -> list of calling-class names
        self.ctor_call_sites: Dict[int, List[Optional[str]]] = {}

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

    def _get_arg_types(self, args) -> List[Optional[object]]:
        """
        Extract the actual (inferred) type from a list of CallArgument nodes.
        Returns None for any argument whose type cannot be determined.
        """
        result = []
        for arg in args:
            try:
                ti = arg.expr.get_type_info()
                if ti is None:
                    result.append(None)
                else:
                    _, actual_t = ti
                    result.append(actual_t)
            except Exception:
                result.append(None)
        return result

    def _args_match_params(self, arg_types, params) -> bool:
        """
        Return True if every argument type in *arg_types* is a subtype of
        the corresponding parameter type in *params*, with the following
        lenient rules:
        - Arity must match exactly.
        - An unknown argument type (None) is treated as compatible with any
          parameter type.
        - If the parameter type is a type variable or wildcard, any argument
          type is accepted.
        - If the subtype check raises an exception, the argument is treated
          as compatible (conservative / sound choice for our soundness
          analysis).
        """
        if len(arg_types) != len(params):
            return False
        for arg_t, param in zip(arg_types, params):
            if arg_t is None:
                continue  # Unknown — assume compatible.
            param_t = param.param_type
            if param_t is None:
                continue
            if param_t.is_type_var() or param_t.is_wildcard():
                continue  # Generic parameter — accept any type.
            try:
                if not arg_t.is_subtype(param_t):
                    return False
            except Exception:
                continue  # Cannot determine — assume compatible.
        return True

    def _find_method_in_hierarchy(
            self, class_name: str, method_name: str,
            arg_types: Optional[List] = None,
    ) -> Optional[ast.FunctionDeclaration]:
        """
        Walk the inheritance chain of *class_name* (breadth-first) looking
        for a method named *method_name*.

        When *arg_types* is provided, only methods whose parameter types are
        compatible with those argument types (via subtyping) are considered.
        This handles overloaded methods correctly.

        When *arg_types* is None (e.g. from a function reference where arity
        is not known), the first method with a matching name is returned.
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
                    if func.name != method_name:
                        continue
                    if arg_types is None:
                        return func  # Name-only match (function reference).
                    if self._args_match_params(arg_types, func.params):
                        return func
            queue.extend(self.superclasses.get(cname, []))
        return None

    def _find_constructors(
            self, class_name: str, arg_types: Optional[List] = None,
            num_args: Optional[int] = None,
    ) -> List[ast.Constructor]:
        """
        Return constructors of *class_name* that are compatible with the
        given call site.

        When *arg_types* is provided (direct ``new`` call), only constructors
        whose parameter types are all supertypes of the corresponding
        argument types are returned — handling overloads with the same arity
        but different parameter types correctly.

        When *arg_types* is None but *num_args* is given (constructor
        reference where argument types are unknown), fall back to arity-only
        matching.

        When both are None, all constructors are returned (used when recording
        all constructors for a constructor reference ``ClassName::new``).
        """
        class_decl = self.class_decls.get(class_name)
        if class_decl is None:
            return []
        if arg_types is not None:
            return [c for c in class_decl.constructors
                    if self._args_match_params(arg_types, c.params)]
        if num_args is not None:
            return [c for c in class_decl.constructors
                    if len(c.params) == num_args]
        return list(class_decl.constructors)

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

    def _record_call_site(self, receiver, func_name: str, args=None):
        """
        Shared logic for visit_func_call and visit_func_ref: given a receiver
        expression and the referenced method name, record a call site entry if
        the method belongs to a known class.

        When *args* (a list of CallArgument nodes) is provided, type-aware
        overload resolution is used so that only the matching overload is
        recorded as called.  When *args* is None (e.g. from a function
        reference), the first method with a matching name is recorded.

        Constructor references (func_name == _NEW_REF) are handled separately
        in visit_new / visit_func_ref.
        """
        if receiver is None or func_name == _NEW_REF:
            return
        type_info = receiver.get_type_info()
        if type_info is None:
            return
        _, actual_t = type_info
        class_name = _get_class_name(actual_t)
        if class_name is None:
            return
        arg_types = self._get_arg_types(args) if args is not None else None
        func_decl = self._find_method_in_hierarchy(class_name, func_name,
                                                   arg_types)
        if func_decl is not None:
            self.call_sites.setdefault(id(func_decl), []).append(
                self.current_class
            )

    def _record_ctor_call_site(self, class_name: str, args):
        """
        Record a constructor call site for the constructor(s) of *class_name*
        that match the given arguments via type-aware overload resolution.

        *args* is a list of CallArgument nodes from the ``New`` expression.
        """
        arg_types = self._get_arg_types(args)
        for ctor in self._find_constructors(class_name, arg_types=arg_types):
            self.ctor_call_sites.setdefault(id(ctor), []).append(
                self.current_class
            )

    def visit_func_call(self, node: ast.FunctionCall):
        super().visit_func_call(node)
        self._record_call_site(node.receiver, node.func, node.args)

    def visit_func_ref(self, node: ast.FunctionReference):
        super().visit_func_ref(node)
        if node.func == _NEW_REF:
            # Constructor reference: ClassName::new.
            # We don't know the arity from the reference alone, so record
            # for all constructors of the referenced class.
            if node.receiver is None:
                return
            type_info = node.receiver.get_type_info()
            if type_info is None:
                return
            _, actual_t = type_info
            class_name = _get_class_name(actual_t)
            if class_name is None:
                return
            for ctor in self._find_constructors(class_name):
                self.ctor_call_sites.setdefault(id(ctor), []).append(
                    self.current_class
                )
        else:
            self._record_call_site(node.receiver, node.func)

    def visit_new(self, node: ast.New):
        super().visit_new(node)
        class_name = _get_class_name(node.class_type)
        if class_name is None:
            return
        self._record_ctor_call_site(class_name, node.args)


class AccessibilityErrorEnumerator(ErrorEnumerator):
    """
    Injects accessibility violations by tightening a method's or constructor's
    access modifier to a value that is guaranteed to make at least one existing
    call site ill-typed.

    The access modifier hierarchy (most → least permissive):
        public  →  protected  →  private

    Soundness conditions for each injected modifier:
    * ``protected``: sound when there is at least one call from a class that
      is *not* the defining class/nest and *not* a subclass of it.
    * ``private``: sound when there is at least one call from any class
      outside the defining class's nest.
    """

    name = "AccessibilityErrorEnumerator"
    ACCESS_MODIFIERS = ACCESS_MODIFIERS

    def __init__(self, program: ast.Program,
                 program_gen: ProgramGenerator,
                 bt_factory: BuiltinFactory,
                 options: dict = None):
        self.analysis = AccessibilityAnalysis()
        self.options = options or {}
        # Populated by add_err_message; may be FunctionDeclaration or Constructor.
        self._error_decl: Optional[Union[ast.FunctionDeclaration,
                                         ast.Constructor]] = None
        self._error_class_decl: Optional[ast.ClassDeclaration] = None
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
        if self._error_decl is None:
            return None
        if isinstance(self._error_decl, ast.Constructor):
            member = f"{self._error_class_decl.name}.<init>"
        else:
            member = f"{self._error_class_decl.name}.{self._error_decl.name}"
        return (
            f"Added accessibility error using {self.name}:\n"
            f" - Member: {member}\n"
            f" - Original access modifier: {self.original_access_mod}\n"
            f" - Injected access modifier: {self.injected_access_mod}\n"
        )

    # ------------------------------------------------------------------
    # ErrorEnumerator interface
    # ------------------------------------------------------------------

    def add_err_message(self, loc: Loc, new_access_mod, *args):
        """Record which declaration was mutated and how."""
        if isinstance(loc, CtorLoc):
            self._error_decl = loc.ctor
        else:
            self._error_decl = loc.func_decl
        self._error_class_decl = loc.class_decl
        self.injected_access_mod = new_access_mod

    def get_candidate_program_locations(self) -> List[Loc]:
        """Return every class method and constructor as a candidate location."""
        self.analysis.visit_program(self.program)
        locations: List[Loc] = []
        for class_decl in self.analysis.class_decls.values():
            for func in class_decl.functions:
                if func.func_type == ast.FunctionDeclaration.CLASS_METHOD:
                    locations.append(FuncLoc(func_decl=func,
                                             class_decl=class_decl))
            for ctor in class_decl.constructors:
                locations.append(CtorLoc(ctor=ctor, class_decl=class_decl))
        return locations

    def filter_program_locations(self, locations: List[Loc]) -> List[Loc]:
        """
        Keep only locations where it is sound to inject an accessibility error,
        i.e. the method/constructor has at least one call site from outside its
        defining class's nest.
        """
        self.metadata["locations"] = len(locations)
        filtered: List[Loc] = []

        for loc in locations:
            if isinstance(loc, CtorLoc):
                decl = loc.ctor
                call_sites_map = self.analysis.ctor_call_sites
            else:
                decl = loc.func_decl
                call_sites_map = self.analysis.call_sites

            # Cannot tighten already-private declarations.
            access_mod = decl.metadata.get("access_mod", "public")
            if access_mod == "private":
                continue

            # Abstract methods (no body) cannot be given a tighter modifier
            # in a sound way because they are overridden in subclasses.
            # (Constructors always have a body.)
            if isinstance(loc, FuncLoc) and decl.body is None:
                continue

            all_calls = call_sites_map.get(id(decl), [])
            if not all_calls:
                continue  # Never called – no error can be guaranteed.

            # Calls from outside the defining class's nest are the only ones
            # that can guarantee an accessibility error (nest members have
            # unrestricted access to private/protected members in Java 11+).
            external_calls = [
                c for c in all_calls
                if not self._are_nest_members(c, loc.class_decl.name)
            ]
            if not external_calls:
                continue

            filtered.append(loc)

        self.metadata["examined"] = len(filtered)
        return filtered

    def get_programs_with_error(self, loc: Loc):
        """
        Yield program variants where the access modifier of the method or
        constructor has been changed to a more restrictive value, in order of
        decreasing permissiveness (public → protected → private).
        """
        class_decl = loc.class_decl
        if isinstance(loc, CtorLoc):
            decl = loc.ctor
            call_sites_map = self.analysis.ctor_call_sites
        else:
            decl = loc.func_decl
            call_sites_map = self.analysis.call_sites

        original_access_mod = decl.metadata.get("access_mod", "public")
        self.original_access_mod = original_access_mod

        all_calls = call_sites_map.get(id(decl), [])

        # Calls from outside the defining class's nest.
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
                # that is neither the defining class/nest nor one of its subclasses.
                if new_mod == "protected" and not outside_calls:
                    continue

                # private: accessible only from the defining class's nest.
                # An error is guaranteed whenever there is any external call.
                if new_mod == "private" and not external_calls:
                    continue
                # ----------------------------------------------------------------

                decl.metadata["access_mod"] = new_mod
                self.add_err_message(loc, new_mod)
                yield self.program

        finally:
            # Always restore the original access modifier so subsequent
            # locations see an unmodified program.
            decl.metadata["access_mod"] = original_access_mod

    def enumerate_programs(self):
        """
        Override the base-class implementation because our locations are
        function/constructor declarations (not expressions) and do not need
        the ASTExprUpdate restoration mechanism used by ErrorEnumerator.
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
