"""
Constraint-based type recovery for error enumeration.

When an injection location is governed by an element whose type is omitted (an
unannotated variable, or a polymorphic call with omitted type arguments), the
expected type cannot be read off directly. We recover a hint for the element
by collecting subtyping constraints from its usages and solving them; the hint
drives the enumeration of incompatible types without touching the program.
Elements with no constraining usage are left unconstrained (hint None) and are
skipped, since no type mismatch can be injected there.
"""
from typing import Callable, Dict, List, Optional, Set, Tuple

from src.ir import ast, types as tp, type_utils as tu
from src.ir.builtins import BuiltinFactory


RECEIVER_INDEX = -1

# Safety net against degenerate programs; real demand chains are short.
MAX_CHAIN_DEPTH = 12

# Members provided by extension mechanisms (Groovy GDK, Kotlin scope
# functions, Scala enrichment) or by Object. Accessing one of them places no
# reliable demand, since any replacement type still provides the member.
EXTENSION_MEMBERS = {
    "groovy": {
        "any", "asBoolean", "asType", "collect", "dump", "each",
        "eachWithIndex", "equals", "every", "find", "findAll",
        "findIndexOf", "findResult", "getAt", "getClass", "getMetaClass",
        "getProperties", "grep", "hasProperty", "hashCode", "identity",
        "inject", "inspect", "invokeMethod", "is", "isCase", "iterator",
        "metaClass", "notify", "notifyAll", "print", "printf", "println",
        "properties", "putAt", "respondsTo", "sleep", "split", "sprintf",
        "tap", "toString", "use", "wait", "with", "withTraits", "class",
    },
    "kotlin": {
        "also", "apply", "equals", "hashCode", "let", "run", "takeIf",
        "takeUnless", "to", "toString",
    },
    "scala": {
        "asInstanceOf", "ensuring", "eq", "equals", "formatted", "getClass",
        "hashCode", "isInstanceOf", "ne", "notify", "notifyAll",
        "synchronized", "toString", "wait", "->",
    },
    "java": {
        "equals", "getClass", "hashCode", "notify", "notifyAll", "toString",
        "wait",
    },
}


class Demand():
    """
    The demand a usage places on a tracked element. `type` is the demanded
    type (it drives incompatible-type enumeration and most-specific hint
    selection); `check`, when set, refines breakage so that `allows(t)` is
    True only when a replacement of type t certainly makes the usage
    ill-typed; `key` identifies the demand for consensus across overloads.
    """

    PLAIN = "plain"

    def __init__(self, demanded_type: tp.Type, kind: str = PLAIN,
                 key: tuple = None,
                 check: Callable[[tp.Type], bool] = None):
        self.type = demanded_type
        self.kind = kind
        self.key = key if key is not None else (kind, demanded_type)
        self.check = check
        # For member demands: (member declarations, demand on the member
        # access result), used to thread demands through member results.
        self.member_link = None

    @property
    def is_plain(self) -> bool:
        return self.check is None

    def allows(self, candidate: tp.Type) -> bool:
        """
        True when replacing the element with `candidate` certainly breaks the
        usage. Plain demands rely on the enumeration's own `candidate <!: type`
        guarantee, so they allow every candidate.
        """
        if self.check is None:
            return True
        try:
            return bool(self.check(candidate))
        except Exception:
            return False

    def satisfied_by(self, t: tp.Type) -> bool:
        """
        Whether a value of type t satisfies the usage. The seed's actual type
        satisfies every demand, so this also serves as a sanity invariant.
        """
        if t is None:
            return True
        if self.check is not None:
            return not self.allows(t)
        if t == self.type:
            return True
        try:
            return (t.is_subtype(self.type) or t.is_assignable(self.type)
                    or t.box_type().is_subtype(self.type.box_type()))
        except Exception:
            return True

    def __eq__(self, other):
        return isinstance(other, Demand) and self.key == other.key

    def __hash__(self):
        return hash(self.key)

    def __str__(self):
        if self.kind == self.PLAIN:
            return str(self.type)
        return f"{self.type} [{self.kind}]"


def children_with_indices(node: ast.Node) -> List[Tuple[ast.Node, int]]:
    """
    Children of an AST node paired with the index convention used by `Loc`
    and `ASTExprUpdate` (receivers at -1, conditional branches at 1 and 2).
    """
    if isinstance(node, ast.Block):
        return [(stmt, i) for i, stmt in enumerate(node.body)]
    if isinstance(node, ast.VariableDeclaration):
        return [(node.expr, 0)]
    if isinstance(node, ast.FieldDeclaration):
        return []
    if isinstance(node, ast.FunctionDeclaration):
        children = [(p.default, i) for i, p in enumerate(node.params)
                    if p.default is not None]
        if node.body is not None:
            children.append((node.body, 0))
        return children
    if isinstance(node, ast.Lambda):
        if node.body is not None:
            return [(node.body, 0)]
        return []
    if isinstance(node, ast.FunctionCall):
        children = []
        if node.receiver is not None:
            children.append((node.receiver, RECEIVER_INDEX))
        for i, arg in enumerate(node.args):
            expr = arg.expr if isinstance(arg, ast.CallArgument) else arg
            children.append((expr, i))
        return children
    if isinstance(node, ast.New):
        children = []
        for i, arg in enumerate(node.args):
            expr = arg.expr if isinstance(arg, ast.CallArgument) else arg
            children.append((expr, i))
        if node.receiver is not None:
            children.append((node.receiver, RECEIVER_INDEX))
        return children
    if isinstance(node, ast.FieldAccess):
        return [(node.expr, 0)] if node.expr else []
    if isinstance(node, ast.FunctionReference):
        # ExprLocationAnalysis indexes function-reference receivers at 0.
        return [(node.receiver, 0)] if node.receiver else []
    if isinstance(node, ast.Assignment):
        children = []
        if node.receiver is not None:
            children.append((node.receiver, RECEIVER_INDEX))
        children.append((node.expr, 0))
        return children
    if isinstance(node, ast.Conditional):
        return [(node.cond, 0), (node.true_branch, 1),
                (node.false_branch, 2)]
    if isinstance(node, ast.MultiConditional):
        children = []
        i = 0
        if node.root_cond is not None:
            children.append((node.root_cond, 0))
            i += 1
        for j, cond in enumerate(node.conditions):
            children.append((cond, i + j))
        i += len(node.conditions)
        for j, branch in enumerate(node.branches):
            children.append((branch, i + j))
        return children
    if isinstance(node, ast.ArrayExpr):
        return [(e, i) for i, e in enumerate(node.exprs)]
    if isinstance(node, ast.UnaryExpr):
        return [(node.expr, 0)]
    if isinstance(node, ast.BinaryExpr):
        return [(node.lexpr, 0), (node.rexpr, 1)]
    if isinstance(node, ast.Return):
        return [(node.expr, 0)] if node.expr else []
    if isinstance(node, ast.Loop):
        children = [(node.block, 0)]
        if node.cond is not None:
            children.append((node.cond, 1))
        return children
    if isinstance(node, ast.TryCatch):
        children = [(node.try_block, 0)]
        for i, block in enumerate(node.catch_blocks.values()):
            children.append((block, i + 1))
        return children
    if isinstance(node, (ast.ClassDeclaration, ast.Constructor,
                         ast.ObjectDecleration)):
        children = []
        for f in getattr(node, "functions", []):
            children.append((f, 0))
        for f in getattr(node, "fields", []):
            children.append((f, 0))
        # Superclass-constructor arguments. Their own nodes have a fragile
        # __hash__, so we hang the arguments off the class instead.
        for sc in getattr(node, "superclasses", []) or []:
            for arg in getattr(sc, "args", None) or []:
                expr = arg.expr if isinstance(arg, ast.CallArgument) else arg
                children.append((expr, 0))
        return children
    return []


class TypeHintRecovery():
    """
    Recovers type hints for elements whose type is omitted, by collecting
    constraints from their usages and solving them via subtype unification.
    """

    def __init__(self, program: ast.Program, api_graph,
                 bt_factory: BuiltinFactory):
        self.program = program
        self.api_graph = api_graph
        self.bt_factory = bt_factory
        self.language = bt_factory.get_language()
        # node -> (parent, index) over the whole program (declarations
        # included). AST nodes have no __eq__, so keys compare by identity.
        self._parents: Dict[ast.Node, Tuple[ast.Node, int]] = None
        # When set to a list, records every (usage, demand) pair examined.
        self.trace = None

    def recover_expected_type(self, expr: ast.Expr) -> Optional[Demand]:
        """
        Demand placed on the expression at an injection location whose
        governing element has no explicit type. None when unconstrained (no
        usage pins the type), so no mismatch can be injected.
        """
        self._ensure_parents()
        return self._demand_of(expr, 0, set())

    def recover_demand_of(self, expr: ast.Expr) -> Optional[Demand]:
        """
        Demand placed by the context of `expr`. Used by receiver replacement
        to decide whether changing a member access's result type mismatches.
        """
        self._ensure_parents()
        return self._demand_of(expr, 0, set())

    def _ensure_parents(self):
        if self._parents is not None:
            return
        self._parents = {}
        stack = list(self.program.declarations)
        for decl in stack:
            self._parents.setdefault(decl, (None, 0))
        while stack:
            node = stack.pop()
            for child, index in children_with_indices(node):
                if child is None:
                    continue
                self._parents[child] = (node, index)
                stack.append(child)

    def _enclosing(self, node: ast.Node, kinds) -> Optional[ast.Node]:
        current = self._parents.get(node)
        while current is not None:
            parent, _ = current
            if parent is None:
                return None
            if isinstance(parent, kinds):
                return parent
            current = self._parents.get(parent)
        return None

    def _plain(self, t: Optional[tp.Type]) -> Optional[Demand]:
        if t is None:
            return None
        return Demand(t)

    @staticmethod
    def _satisfies(t: tp.Type, target: tp.Type) -> bool:
        if t == target:
            return True
        try:
            return (t.is_subtype(target) or t.is_assignable(target)
                    or t.box_type().is_subtype(target.box_type()))
        except Exception:
            return True

    def _demand_of(self, node: ast.Node, depth: int,
                   active: Set[Tuple[int, int]]) -> Optional[Demand]:
        if depth > MAX_CHAIN_DEPTH:
            return None
        entry = self._parents.get(node)
        if entry is None:
            return None
        parent, index = entry

        # An annotated declaration demands its type; an unannotated one takes
        # its demand from the variable's usages.
        if isinstance(parent, ast.VariableDeclaration):
            if not parent.is_type_inferred:
                return self._plain(parent.var_type)
            return self._demand_of_variable(parent, None, depth + 1, active)

        # A body demands the declared return type (an omitted one is beyond
        # intra-method reasoning); a parameter default demands its type.
        if isinstance(parent, ast.FunctionDeclaration):
            if node is parent.body:
                return self._plain(self._return_type_demand(parent))
            for param in parent.params:
                if param.default is node:
                    return self._plain(param.get_type())
            return None

        if isinstance(parent, ast.Return):
            func = self._enclosing(parent, (ast.FunctionDeclaration,
                                            ast.Lambda))
            if isinstance(func, ast.FunctionDeclaration):
                return self._plain(self._return_type_demand(func))
            if isinstance(func, ast.Lambda):
                return self._lambda_return_demand(func, depth, active)
            return None

        # A lambda body is checked against the functional type's return type.
        if isinstance(parent, ast.Lambda):
            return self._lambda_return_demand(parent, depth, active)

        # Call arguments demand the formal parameter type; receivers demand a
        # type providing a compatible member.
        if isinstance(parent, (ast.FunctionCall, ast.New)):
            if index == RECEIVER_INDEX:
                return self._demand_of_receiver(parent, depth, active)
            return self._demand_of_call_arg(parent, index, depth, active)

        if isinstance(parent, ast.FieldAccess):
            return self._demand_of_receiver(parent, depth, active)

        if isinstance(parent, ast.FunctionReference):
            return self._demand_of_receiver(parent, depth, active)

        if isinstance(parent, ast.Assignment):
            if index == RECEIVER_INDEX:
                return self._demand_of_receiver(parent, depth, active)
            return self._demand_of_assignment(parent, depth, active)

        # The condition demands bool; the branches inherit the conditional's
        # own demand (sound, since its type is the LUB of the branches).
        if isinstance(parent, ast.Conditional):
            if index == 0:
                return self._bool_demand()
            if not parent.is_expression:
                return None
            return self._demand_of(parent, depth + 1, active)

        if isinstance(parent, ast.MultiConditional):
            n_conds = len(parent.conditions) + (
                1 if parent.root_cond is not None else 0)
            if index < n_conds:
                if parent.root_cond is not None:
                    # `when (subject) { value -> ... }`: subject and case
                    # values match by equality, which accepts any type.
                    return None
                return self._bool_demand()
            if not parent.is_expression:
                return None
            return self._demand_of(parent, depth + 1, active)

        # The value of a block is its last statement; it is demanded a type
        # only when the block itself sits in a value position.
        if isinstance(parent, ast.Block):
            if not parent.body or parent.body[-1] is not node:
                return None
            return self._demand_of_block_value(parent, depth, active)

        if isinstance(parent, ast.Loop):
            if index == 1:  # loop condition
                return self._bool_demand()
            return None

        if isinstance(parent, ast.ArrayExpr):
            return self._array_element_demand(parent)

        if isinstance(parent, ast.Is):
            # `x is T` accepts operands of any type.
            return None

        if isinstance(parent, (ast.BinaryExpr, ast.UnaryExpr)):
            return self._demand_of_operand(parent, index, depth, active)

        return None

    def _demand_of_block_value(self, block: ast.Block, depth: int,
                               active) -> Optional[Demand]:
        entry = self._parents.get(block)
        if entry is None:
            return None
        parent, index = entry
        if isinstance(parent, ast.Conditional):
            if not parent.is_expression or index == 0:
                return None
            return self._demand_of(parent, depth + 1, active)
        if isinstance(parent, ast.MultiConditional):
            n_conds = len(parent.conditions) + (
                1 if parent.root_cond is not None else 0)
            if not parent.is_expression or index < n_conds:
                return None
            return self._demand_of(parent, depth + 1, active)
        if isinstance(parent, ast.FunctionDeclaration):
            return self._plain(self._return_type_demand(parent))
        if isinstance(parent, ast.Lambda):
            return self._lambda_return_demand(parent, depth, active)
        if isinstance(parent, ast.TryCatch):
            # try/catch as an expression: its value is the blocks' value.
            return self._demand_of(parent, depth + 1, active)
        return None

    def _return_type_demand(
            self, func: ast.FunctionDeclaration) -> Optional[tp.Type]:
        ret_type = func.ret_type
        if ret_type is None:
            return None
        if self._is_unit(ret_type):
            return None
        return ret_type

    def _lambda_return_demand(self, lam: ast.Lambda, depth: int,
                              active) -> Optional[Demand]:
        # If the lambda's context pins its functional type (an annotated
        # variable, a monomorphic parameter), the body demands that type's
        # return type.
        f_demand = self._demand_of(lam, depth + 1, active)
        if f_demand is None:
            return None
        if not f_demand.is_plain and f_demand.kind != "ctor-arg":
            return None
        # For a ctor-arg demand (lambda where C<R> is expected, R inferred),
        # SAM conversion instantiates C with the body's return type, so the
        # body inherits the bound resolved for R via C<bound>.
        f_type = f_demand.type
        func_t = None
        get_func_t = getattr(self.api_graph, "get_functional_type_instantiated",
                             None)
        if get_func_t is not None:
            try:
                func_t = get_func_t(f_type)
            except Exception:
                func_t = None
        if func_t is None and f_type.is_parameterized() \
                and f_type.is_function_type():
            func_t = f_type
        if func_t is None or not func_t.is_parameterized() \
                or not func_t.type_args:
            return None
        ret_t = func_t.type_args[-1]
        if ret_t.has_type_variables() or ret_t.is_wildcard():
            return None
        return self._plain(ret_t)

    def _array_element_demand(self,
                              array_expr: ast.ArrayExpr) -> Optional[Demand]:
        if self.language == "groovy":
            # Groovy list-to-array coercions are too permissive to demand on.
            return None
        array_t = array_expr.array_type
        if array_t is None or not array_t.is_parameterized():
            return None
        elem_t = array_t.type_args[0]
        if elem_t.is_wildcard() or elem_t.has_type_variables():
            return None
        return self._plain(elem_t)

    def _bool_demand(self) -> Optional[Demand]:
        if self.language == "groovy":
            # Groovy-truth: any value can be coerced in a boolean context.
            return None
        return self._plain(self.bt_factory.get_boolean_type())

    def _is_unit(self, t: tp.Type) -> bool:
        return t in (self.bt_factory.get_void_type(primitive=True),
                     self.bt_factory.get_void_type(primitive=False))

    def _operand_demand(self, display: tp.Type,
                        acceptable: List[tp.Type],
                        kind: str) -> Demand:
        names = frozenset(t.name for t in acceptable)

        def check(candidate: tp.Type) -> bool:
            # Breaks the usage only if acceptable in none of the admissible
            # types (boxing-aware).
            for acc in acceptable:
                try:
                    if candidate.is_subtype(acc) or \
                            candidate.is_assignable(acc) or \
                            candidate.box_type().is_subtype(acc.box_type()):
                        return False
                except Exception:
                    return False
            return True

        return Demand(display, kind=kind, key=(kind, names), check=check)

    def _arith_acceptable(self, operator: str,
                          is_left: bool) -> Optional[List[tp.Type]]:
        bt = self.bt_factory
        lang = self.language
        char_types = self._char_types()
        string_t = bt.get_string_type()
        if lang == "groovy":
            # Groovy operator extensions (GDK) accept far more than numeric
            # operands (String*int, List+List, Date+int, ...).
            return None
        if operator == "+":
            if lang == "java":
                # String concatenation accepts any operand on either side.
                return None
            if lang == "kotlin":
                acceptable = self._basic_numerics()
                if is_left:
                    # "s" + x is String.plus(Any); 'c' + 1 is Char.plus(Int).
                    acceptable = acceptable + char_types + [string_t]
                return acceptable
            if lang == "scala":
                # x + "s" and "s" + x are string concatenations, and only
                # numeric/char operands have arithmetic +.
                return self._basic_numerics() + char_types + [string_t]
            return None
        if operator in ("-", "*", "/"):
            if lang == "kotlin":
                acceptable = self._basic_numerics()
                if is_left and operator == "-":
                    acceptable = acceptable + char_types  # 'c' - 1, 'c' - 'd'
                return acceptable
            if lang in ("java", "scala"):
                return self._basic_numerics() + char_types
            return None
        return None

    def _basic_numerics(self) -> List[tp.Type]:
        bt = self.bt_factory
        types = []
        for getter in (bt.get_byte_type, bt.get_short_type,
                       bt.get_integer_type, bt.get_long_type,
                       bt.get_float_type, bt.get_double_type):
            for kwargs in ({}, {"primitive": True}):
                try:
                    t = getter(**kwargs)
                except TypeError:
                    continue
                if t is not None:
                    types.append(t)
        return types

    def _char_types(self) -> List[tp.Type]:
        bt = self.bt_factory
        types = []
        for kwargs in ({}, {"primitive": True}):
            try:
                t = bt.get_char_type(**kwargs)
            except TypeError:
                continue
            if t is not None:
                types.append(t)
        return types

    def _other_operand_type(self, parent: ast.BinaryExpr,
                            index: int) -> Optional[tp.Type]:
        other = parent.rexpr if index == 0 else parent.lexpr
        if isinstance(other, ast.Expr) and other.is_typed():
            pair = other.get_type_info()
            return pair[1] or pair[0]
        return None

    def _demand_of_operand(self, parent, index: int, depth: int,
                           active) -> Optional[Demand]:
        lang = self.language
        bt = self.bt_factory
        operator = getattr(parent, "operator", None)
        symbol = getattr(operator, "name", None)
        if getattr(operator, "is_not", False) and symbol == "=":
            symbol = "!="

        if isinstance(parent, ast.LogicalExpr):
            # Operands of && and || must be boolean (Groovy-truth aside).
            return self._bool_demand()

        if isinstance(parent, ast.EqualityExpr):
            # Equality accepts operands of any type in Kotlin/Groovy/Scala;
            # Java's "incomparable types" rule is a castability check, which
            # an upper bound cannot express soundly.
            return None

        if isinstance(parent, ast.ComparisonExpr):
            if lang == "groovy":
                # Groovy compares via Comparable/GDK and accepts non-numeric
                # operand combinations.
                return None
            other_t = self._other_operand_type(parent, index)
            acceptable = self._basic_numerics() + self._char_types()
            if other_t is None or not self._is_acceptable_in(
                    other_t, acceptable):
                # The fixed operand is not numeric: the operator's typing is
                # driven by Comparable instances we do not model.
                return None
            return self._operand_demand(bt.get_number_type(), acceptable,
                                        "numeric-comparison")

        if isinstance(parent, ast.ArithExpr):
            other_t = self._other_operand_type(parent, index)
            acceptable = self._arith_acceptable(symbol, is_left=(index == 0))
            if acceptable is None:
                return None
            base_numeric = self._basic_numerics() + self._char_types()
            if other_t is None or not self._is_acceptable_in(other_t,
                                                             base_numeric):
                return None
            return self._operand_demand(bt.get_number_type(), acceptable,
                                        f"numeric-arith-{symbol}-{index}")

        if isinstance(parent, ast.UnaryExpr):
            if symbol == "!" and not isinstance(parent, ast.Is):
                return self._bool_demand()
            if symbol == "!!":
                # Kotlin's t!! has t's non-nullable type, so the demand
                # transfers to the operand's base type.
                result = self._demand_of(parent, depth + 1, active)
                return self._base_type_demand(result, "notnull-assert")
            return None

        # Generic BinaryExpr: elvis and subscripts.
        if symbol == "?:":
            result = self._demand_of(parent, depth + 1, active)
            if result is None:
                return None
            if index == 1:
                # The right operand's type flows to the result as-is.
                return result
            # The left operand contributes its non-nullable type, so its
            # demand is against the base type.
            return self._base_type_demand(result, "elvis-left")

        if symbol == "[]":
            if index != 1:
                # The subscripted operand may be an array or a list.
                return None
            if lang == "groovy":
                # GDK getAt accepts ranges, collections, etc.
                return None
            if lang == "kotlin":
                return self._plain(self.bt_factory.get_integer_type())
            # Java array indices: int after unary numeric promotion.
            acceptable = [
                t for t in self._basic_numerics()
                if t.name in {self.bt_factory.get_byte_type().name,
                              self.bt_factory.get_short_type().name,
                              self.bt_factory.get_integer_type().name}
            ] + self._char_types()
            return self._operand_demand(self.bt_factory.get_integer_type(),
                                        acceptable, "subscript-index")

        return None

    def _base_type_demand(self, result: Optional[Demand],
                          kind: str) -> Optional[Demand]:
        if result is None:
            return None
        inner = result

        def check(candidate: tp.Type) -> bool:
            base = candidate
            if candidate.is_nullable():
                base = candidate.type_args[0]
            if inner.check is not None:
                return inner.allows(base)
            return not self._satisfies(base, inner.type)

        return Demand(inner.type, kind=kind, key=(kind, inner.key),
                      check=check)

    def _is_acceptable_in(self, t: tp.Type,
                          acceptable: List[tp.Type]) -> bool:
        for acc in acceptable:
            try:
                if t.is_subtype(acc) or t.is_assignable(acc) or \
                        t.box_type().is_subtype(acc.box_type()):
                    return True
            except Exception:
                continue
        return False

    def _demand_of_receiver(self, parent: ast.Expr, depth: int,
                            active) -> Optional[Demand]:
        if isinstance(parent, ast.FunctionCall):
            member = parent.func.rsplit(".", 1)[-1]
            args = [a.expr if isinstance(a, ast.CallArgument) else a
                    for a in parent.args]
            arg_types = []
            for arg in args:
                if not isinstance(arg, ast.Expr) or not arg.is_typed():
                    return None
                pair = arg.get_type_info()
                arg_types.append(pair[1] or pair[0])
            if any(t is None for t in arg_types):
                return None
        elif isinstance(parent, ast.FieldAccess):
            member, arg_types = parent.field, None
        elif isinstance(parent, ast.FunctionReference):
            member, arg_types = parent.func.rsplit(".", 1)[-1], None
        elif isinstance(parent, ast.Assignment):
            member, arg_types = parent.name, None
        else:
            return None

        if member in EXTENSION_MEMBERS.get(self.language, set()):
            return None

        receiver = parent.expr if isinstance(parent, ast.FieldAccess) \
            else parent.receiver
        rec_type = None
        if receiver is not None and isinstance(receiver, ast.Expr) and \
                receiver.is_typed():
            pair = receiver.get_type_info()
            rec_type = pair[1] or pair[0]
        if rec_type is None:
            return None

        demand = Demand(
            rec_type, kind="member",
            key=("member", member,
                 tuple(t.name for t in arg_types) if arg_types is not None
                 else None),
            check=lambda t: not self._type_defines_member(t, member,
                                                          arg_types))
        # Thread the demand on the member access's own result, so that
        # type-variable resolution can follow chains like f(x).member.
        if isinstance(parent, (ast.FunctionCall, ast.FieldAccess)):
            decls = self._member_decls(rec_type, parent, member, arg_types)
            if decls:
                result_demand = self._demand_of(parent, depth + 1, active)
                if result_demand is not None:
                    demand.member_link = (decls, result_demand)
        return demand

    def _member_decls(self, rec_type: tp.Type, parent: ast.Expr,
                      member: str, arg_types) -> list:
        if isinstance(parent, ast.FieldAccess):
            try:
                field = self.api_graph.get_field(rec_type, member)
            except Exception:
                return []
            return [field] if field is not None else []
        try:
            from src.generators.api import nodes as api_nodes
            dummy = api_nodes.Method(member, None, [], [], {})
            methods = self.api_graph.get_overloaded_methods(
                rec_type, dummy, override_checks_with_self=False)
        except Exception:
            return []
        n_args = len(arg_types or [])
        return [m for m, _ in methods or []
                if len(getattr(m, "parameters", []) or []) == n_args]

    def _type_defines_member(self, t: tp.Type, member: str,
                             arg_types: Optional[List[tp.Type]]) -> bool:
        if t.is_type_constructor():
            return True
        # Fields and parameterless members.
        try:
            field = self.api_graph.get_field(t, member)
        except Exception:
            field = None
        if field is not None:
            return True
        try:
            from src.generators.api import nodes as api_nodes
            dummy = api_nodes.Method(member, None, [], [], {})
            methods = self.api_graph.get_overloaded_methods(
                t, dummy, override_checks_with_self=False)
        except Exception:
            return True
        for method, _ in methods or []:
            params = getattr(method, "parameters", []) or []
            if arg_types is None:
                return True
            if len(params) != len(arg_types):
                continue
            sub = {}
            if t.is_parameterized():
                try:
                    parent_cls = self.api_graph.get_type_by_name(method.cls)
                    sub = tu.get_type_substitution_of_parent(parent_cls, t) \
                        or t.get_type_variable_assignments()
                except Exception:
                    sub = t.get_type_variable_assignments()
            applicable = True
            for arg_t, param in zip(arg_types, params):
                param_t = tp.substitute_type(param.t, sub)
                if param_t.has_type_variables() or param_t.is_wildcard():
                    # Cannot establish inapplicability.
                    continue
                try:
                    if not (arg_t.is_subtype(param_t)
                            or arg_t.is_assignable(param_t)):
                        applicable = False
                        break
                except Exception:
                    continue
            if applicable:
                return True
        # Program-defined classes (no API graph entry): check the program.
        for decl in self.program.declarations:
            if isinstance(decl, ast.ClassDeclaration) and \
                    decl.name == t.name.rsplit(".", 1)[-1]:
                for field in getattr(decl, "fields", []):
                    if field.name == member:
                        return True
                for func in getattr(decl, "functions", []):
                    if func.name == member:
                        return True
        return False

    def _get_overloads(self, call: ast.Expr) -> list:
        try:
            decls = self.api_graph.get_declarations_of_access(
                call, only_instance=False)
        except Exception:
            return []
        return [d for d in decls or []
                if len(getattr(d, "parameters", []) or []) == len(call.args)]

    def _demand_of_call_arg(self, call, index: int, depth: int,
                            active) -> Optional[Demand]:
        args = list(call.args)
        named = [getattr(arg, "name", None) for arg in args]
        if any(name is not None for name in named):
            # Named arguments match formal parameters by name; the API graph
            # has no parameter names, so only program functions qualify.
            return self._named_arg_demand(call, index, named[index])
        overloads = self._get_overloads(call)
        if not overloads:
            return None
        # Overload resolution depends on the argument types this injection is
        # about to change, so the demand is reliable only if all overloads
        # agree on it.
        demands = {
            self._demand_of_call_arg_for_decl(call, decl, index, depth,
                                              active)
            for decl in overloads
        }
        if len(demands) != 1:
            return None
        return demands.pop()

    def _named_arg_demand(self, call, index: int,
                          arg_name: Optional[str]) -> Optional[Demand]:
        if isinstance(call, ast.New) or getattr(call, "receiver", None) \
                is not None:
            return None
        func_name = call.func.rsplit(".", 1)[-1]
        candidates = [
            decl for decl in self._program_functions()
            if decl.name == func_name
        ]
        if len(candidates) != 1:
            return None
        func = candidates[0]
        if func.type_parameters:
            return None
        if arg_name is not None:
            params = [p for p in func.params if p.name == arg_name]
            if len(params) != 1:
                return None
            param_t = params[0].get_type()
        else:
            if index >= len(func.params):
                return None
            param_t = func.params[index].get_type()
        if param_t is None or param_t.has_type_variables() or \
                param_t.is_wildcard():
            return None
        return self._plain(param_t)

    def _program_functions(self) -> List[ast.FunctionDeclaration]:
        functions = []
        for decl in self.program.declarations:
            if isinstance(decl, ast.FunctionDeclaration):
                functions.append(decl)
            elif isinstance(decl, ast.ClassDeclaration):
                functions.extend(getattr(decl, "functions", []))
        return functions

    def _demand_of_call_arg_for_decl(self, call, decl, index: int,
                                     depth: int, active) -> Optional[Demand]:
        parameters = getattr(decl, "parameters", None)
        if not parameters or index >= len(parameters):
            return None
        param_t = parameters[index].t
        # Substitute class-level type variables from the receiver's type
        # arguments (or the constructed type's, for `new`).
        class_sub = self._class_substitution(call)
        param_t = tp.substitute_type(param_t, class_sub)

        decl_type_params = self._call_type_parameters(call, decl)
        free_tvars = [
            t for t in tu.get_type_variables_of_type(param_t, self.bt_factory)
            if t in decl_type_params
        ]
        if not free_tvars:
            if param_t.has_type_variables() or param_t.is_wildcard():
                # Type variables we cannot attribute to the call: be safe.
                return None
            return self._plain(param_t)

        # Explicit type arguments: substitute them into the parameter type.
        explicit_sub = self._explicit_type_args(call, decl_type_params)
        if explicit_sub is not None:
            demand = tp.substitute_type(param_t, explicit_sub)
            if demand.has_type_variables():
                return None
            return self._plain(demand)

        # Omitted type arguments: resolve the type variables from the demand
        # on the call's result and from their declared bounds.
        if param_t.is_type_var():
            return self._bare_tvar_demand(call, decl, param_t, class_sub,
                                          depth, active)
        return self._generic_param_demand(call, decl, param_t, class_sub,
                                          depth, active)

    def _resolve_tvar_bound(self, call, decl, tvar: tp.TypeParameter,
                            class_sub: dict, depth: int,
                            active) -> Tuple[Optional[Demand],
                                             Optional[tp.Type]]:
        declared_bound = None
        bound = tvar.get_bound_rec(self.bt_factory)
        if bound is not None and not bound.has_type_variables() \
                and not bound.is_wildcard():
            declared_bound = tp.substitute_type(bound, class_sub)

        out_t = self._call_output_type(call, decl)
        if out_t is None:
            return None, declared_bound
        out_t = tp.substitute_type(out_t, class_sub)
        result_demand = self._demand_of(call, depth + 1, active)
        if result_demand is None:
            return None, declared_bound
        if out_t.is_type_var() and out_t == tvar:
            # The result type is the type variable itself, so any demand on
            # the result (plain or not) is a demand on the type variable.
            return result_demand, declared_bound
        tvars_of_call = set(tu.get_type_variables_of_type(out_t,
                                                          self.bt_factory))
        sub = self._solve_result_demand(out_t, result_demand, tvars_of_call)
        if not sub:
            return None, declared_bound
        resolved = sub.get(tvar)
        if resolved is None or resolved.is_wildcard() or \
                (resolved.is_type_var() and resolved in
                 self._call_type_parameters(call, decl)):
            return None, declared_bound
        return self._plain(resolved), declared_bound

    def _solve_result_demand(self, sym_t: tp.Type, demand: Optional[Demand],
                             tvars: Set[tp.TypeParameter],
                             depth: int = 0) -> Optional[dict]:
        if demand is None or depth > 4:
            return None
        if not self._occurrences_safe(sym_t, tvars):
            return None
        if demand.is_plain:
            sub = tu.unify_types(demand.type, sym_t, self.bt_factory,
                                 same_type=False, subtype_on_left=False)
            return sub or None
        if demand.kind == "member" and demand.member_link:
            decls, inner = demand.member_link
            if not decls or inner is None:
                return None
            solutions = []
            for decl in decls:
                try:
                    ret = self.api_graph.get_concrete_output_type(decl)
                except Exception:
                    return None
                if ret is None:
                    return None
                member_tvars = set(getattr(decl, "type_parameters", []) or [])
                if member_tvars & set(tu.get_type_variables_of_type(
                        ret, self.bt_factory)):
                    # The member's own type parameters get re-inferred and can
                    # rescue the replacement.
                    return None
                sub_owner = self._owner_substitution(decl, sym_t)
                if sub_owner is None:
                    return None
                ret_sym = tp.substitute_type(ret, sub_owner)
                solution = self._solve_result_demand(ret_sym, inner, tvars,
                                                     depth + 1)
                if not solution:
                    return None
                solutions.append(solution)
            first = solutions[0]
            for other in solutions[1:]:
                if other != first:
                    return None
            return first
        return None

    def _owner_substitution(self, decl, sym_t: tp.Type) -> Optional[dict]:
        owner = None
        try:
            owner = self.api_graph.get_type_by_name(decl.cls)
        except Exception:
            owner = None
        if owner is None:
            return None
        if not owner.is_type_constructor():
            return {}
        if sym_t.is_parameterized() and sym_t.t_constructor.name == owner.name:
            return {param: arg for param, arg in
                    zip(owner.type_parameters, sym_t.type_args)}
        try:
            sub = tu.get_type_substitution_of_parent(owner, sym_t)
        except Exception:
            return None
        return sub or None

    def _occurrences_safe(self, t: tp.Type,
                          tvars: Set[tp.TypeParameter]) -> bool:
        def occurrences(ty, tv):
            if ty == tv:
                return 1
            total = 0
            if ty.is_wildcard() and ty.bound is not None:
                total += occurrences(ty.bound, tv)
            elif ty.is_parameterized():
                for arg in ty.type_args:
                    total += occurrences(arg, tv)
            return total

        for tv in tvars:
            count = occurrences(t, tv)
            if count == 0:
                continue
            if count > 1:
                return False
            if t == tv:
                continue
            if not t.is_parameterized():
                return False
            direct_ok = False
            for i, arg in enumerate(t.type_args):
                param = t.t_constructor.type_parameters[i]
                inner, contravariant_use = arg, False
                if arg.is_wildcard():
                    contravariant_use = arg.is_contravariant()
                    inner = arg.bound if arg.bound is not None else arg
                if inner == tv:
                    if param.is_contravariant() or contravariant_use:
                        return False
                    direct_ok = True
            if not direct_ok:
                # The occurrence is nested deeper than a direct argument.
                return False
        return True

    def _contains_any_tvar(self, t: tp.Type, tvars) -> bool:
        if t is None or not tvars:
            return False
        present = set(tu.get_type_variables_of_type(t, self.bt_factory))
        return bool(present & set(tvars))

    def _bare_tvar_demand(self, call, decl, tvar: tp.TypeParameter,
                          class_sub: dict, depth: int,
                          active) -> Optional[Demand]:
        result_demand, declared_bound = self._resolve_tvar_bound(
            call, decl, tvar, class_sub, depth, active)
        # A bound mentioning the call's own (uninstantiated) type variables is
        # not a type; reject it (it comes from self-referential chains).
        own = self._call_type_parameters(call, decl)
        demands = [d for d in (result_demand, self._plain(declared_bound))
                   if d is not None
                   and not self._contains_any_tvar(d.type, own)]
        return self._most_specific(demands)

    def _generic_param_demand(self, call, decl, param_t: tp.Type,
                              class_sub: dict, depth: int,
                              active) -> Optional[Demand]:
        if not param_t.is_parameterized():
            return None
        t_con = param_t.t_constructor
        decl_type_params = self._call_type_parameters(call, decl)
        tvar, position = None, None
        for i, t_arg in enumerate(param_t.type_args):
            arg = t_arg
            variance_ok = True
            if arg.is_wildcard():
                if arg.is_contravariant():
                    variance_ok = False
                arg = arg.bound if arg.bound is not None else arg
            if arg.is_type_var() and arg in decl_type_params:
                if tvar is not None:
                    return None  # more than one occurrence
                t_param = t_con.type_parameters[i]
                if t_param.is_contravariant() or not variance_ok:
                    # Contravariant positions are rescuable with Nothing.
                    return None
                tvar, position = arg, i
            elif arg.has_type_variables():
                return None
        if tvar is None:
            return None
        # The type variable must not occur elsewhere in this parameter.
        occurrences = [
            t for t in tu.get_type_variables_of_type(param_t, self.bt_factory)
            if t == tvar
        ]
        if len(occurrences) != 1:
            return None

        result_demand, declared_bound = self._resolve_tvar_bound(
            call, decl, tvar, class_sub, depth, active)
        own = self._call_type_parameters(call, decl)
        bound_candidates = []
        if result_demand is not None and result_demand.is_plain and \
                not self._contains_any_tvar(result_demand.type, own):
            bound_candidates.append(result_demand.type)
        if declared_bound is not None and \
                not self._contains_any_tvar(declared_bound, own):
            bound_candidates.append(declared_bound)
        if not bound_candidates:
            return None
        bound = bound_candidates[0]
        for cand in bound_candidates[1:]:
            try:
                if cand.is_subtype(bound):
                    bound = cand
            except Exception:
                continue
        display = tp.substitute_type(param_t, {tvar: bound})
        con_name = t_con.name
        pos = position
        recovery = self

        def check(candidate: tp.Type) -> bool:
            return recovery._violates_constructor_bound(candidate, con_name,
                                                        pos, bound)

        return Demand(display, kind="ctor-arg",
                      key=("ctor-arg", con_name, pos, bound), check=check)

    def _violates_constructor_bound(self, candidate: tp.Type, con_name: str,
                                    position: int, bound: tp.Type) -> bool:
        if candidate.is_type_constructor():
            return False
        supertypes = [candidate]
        try:
            supertypes += list(candidate.get_supertypes())
        except Exception:
            return False
        instantiation = None
        for st in supertypes:
            if st.name == con_name and st.is_parameterized():
                instantiation = st
                break
            if st.name == con_name:
                return False  # raw/unparameterized supertype: be safe
        if instantiation is None:
            return True
        if position >= len(instantiation.type_args):
            return False
        q = instantiation.type_args[position]
        if q.is_wildcard():
            q = q.bound
            if q is None:
                return False
        if q is None or q.has_type_variables():
            return False
        return not self._satisfies(q, bound)

    def _class_substitution(self, call) -> dict:
        if isinstance(call, ast.New):
            class_type = call.class_type
            if class_type.is_parameterized() \
                    and not class_type.can_infer_type_args:
                return class_type.get_type_variable_assignments()
            return {}
        receiver = getattr(call, "receiver", None)
        if receiver is not None and receiver.is_typed():
            rec_t = receiver.get_type_info()[1]
            if rec_t is not None and rec_t.is_parameterized():
                return rec_t.get_type_variable_assignments()
        return {}

    def _call_type_parameters(self, call, decl) -> List[tp.TypeParameter]:
        if isinstance(call, ast.New):
            class_type = call.class_type
            if class_type.is_parameterized() and \
                    class_type.can_infer_type_args:
                return list(class_type.t_constructor.type_parameters)
            return []
        return list(getattr(decl, "type_parameters", []) or [])

    def _explicit_type_args(self, call, decl_type_params) -> Optional[dict]:
        if isinstance(call, ast.New):
            class_type = call.class_type
            if class_type.is_parameterized() \
                    and not class_type.can_infer_type_args:
                return class_type.get_type_variable_assignments()
            return None
        if call.can_infer_type_args or not call.type_args:
            return None
        if len(call.type_args) != len(decl_type_params):
            return None
        return {t_param: call.type_args[i]
                for i, t_param in enumerate(decl_type_params)}

    def _call_output_type(self, call, decl) -> Optional[tp.Type]:
        if isinstance(call, ast.New):
            class_type = call.class_type
            if not class_type.is_parameterized():
                return class_type
            t_con = class_type.t_constructor
            return t_con.new(list(t_con.type_parameters))
        try:
            return self.api_graph.get_concrete_output_type(decl)
        except Exception:
            return None

    def _demand_of_assignment(self, assign: ast.Assignment, depth: int,
                              active) -> Optional[Demand]:
        if assign.receiver is not None:
            # Field assignment: the demand is the field's declared type.
            decl = None
            try:
                decl = self.api_graph.get_declaration_of_access(
                    assign, only_instance=False)
            except Exception:
                decl = None
            if decl is None:
                return self._plain(self._field_type_demand(assign.name))
            sub = {}
            if assign.receiver.is_typed():
                rec_t = assign.receiver.get_type_info()[1]
                if rec_t is not None and rec_t.is_parameterized():
                    sub = rec_t.get_type_variable_assignments()
            try:
                out_t = self.api_graph.get_concrete_output_type(decl)
            except Exception:
                return None
            if out_t is None:
                return None
            out_t = tp.substitute_type(out_t, sub)
            if out_t.has_type_variables():
                return None
            return self._plain(out_t)

        target = self._find_var_decl(assign.name, assign)
        if target is None:
            return None
        if isinstance(target, ast.ParameterDeclaration):
            return self._plain(target.get_type())
        if isinstance(target, ast.FieldDeclaration):
            # Fields are explicitly typed; check against the declared type.
            field_t = target.field_type
            if field_t is None or field_t.has_type_variables():
                return None
            return self._plain(field_t)
        if not target.is_type_inferred:
            return self._plain(target.var_type)
        # Unannotated target: the assignment introduces a new SSA version
        # whose demand comes from the usages it reaches.
        demand = self._demand_of_variable(target, assign, depth + 1, active)
        if demand is not None:
            return demand
        if self.language != "groovy":
            # Without assignment-driven flow typing (Kotlin, Java, Scala), the
            # assignment must conform to the declaration-inferred type, which
            # this injection does not affect.
            return self._plain(target.get_type())
        return None

    def _find_var_decl(self, name: str, anchor: ast.Node):
        node = anchor
        entry = self._parents.get(node)
        while entry is not None:
            parent, _ = entry
            if parent is None:
                break
            if isinstance(parent, ast.Block):
                for stmt in parent.body:
                    if isinstance(stmt, ast.VariableDeclaration) \
                            and stmt.name == name:
                        return stmt
            if isinstance(parent, (ast.FunctionDeclaration, ast.Lambda)):
                for param in parent.params:
                    if param.name == name:
                        return param
            if isinstance(parent, ast.ClassDeclaration):
                for field in getattr(parent, "fields", []):
                    if field.name == name:
                        return field
            node = parent
            entry = self._parents.get(node)
        # Global (top-level) variables may be assigned under a qualified
        # name (Main.x = ...).
        simple = name.rsplit(".", 1)[-1]
        for decl in self.program.declarations:
            if isinstance(decl, ast.VariableDeclaration) and \
                    decl.name in (name, simple):
                return decl
        return None

    def _field_type_demand(self, field_name: str) -> Optional[tp.Type]:
        candidates = [
            field
            for decl in self.program.declarations
            if isinstance(decl, ast.ClassDeclaration)
            for field in getattr(decl, "fields", [])
            if field.name == field_name
        ]
        if len(candidates) != 1:
            return None
        field_t = candidates[0].field_type
        if field_t is None or field_t.has_type_variables():
            return None
        return field_t

    def _demand_of_variable(self, decl: ast.VariableDeclaration,
                            anchor: Optional[ast.Assignment], depth: int,
                            active) -> Optional[Demand]:
        if depth > MAX_CHAIN_DEPTH:
            return None
        key = (id(decl), id(anchor))
        if key in active:
            return None
        active.add(key)
        try:
            usages = self._collect_live_usages(decl, anchor)
            if usages is None:
                return None
            demands = []
            for usage in usages:
                demand = self._demand_of(usage, depth + 1, active)
                if self.trace is not None:
                    self.trace.append((decl, usage, demand))
                if demand is not None:
                    demands.append(demand)
            return self._most_specific(demands)
        finally:
            active.discard(key)

    def _collect_live_usages(self, decl: ast.VariableDeclaration,
                             anchor: Optional[ast.Assignment]):
        start = anchor if anchor is not None else decl
        entry = self._parents.get(start)
        if entry is None or entry[0] is None:
            # Global declaration: with no enclosing block we cannot reason
            # about reassignments, so only final variables are tracked.
            if anchor is None and decl.is_final:
                usages = []
                for top_decl in self.program.declarations:
                    self._scan(top_decl, decl, True, usages, [False])
                return usages
            return None

        usages = []
        lambda_unsafe = [False]
        # Walk outwards, scanning the rest of each enclosing block after the
        # anchor, up to the enclosing callable (scoping bounds the search).
        node = start
        alive = True
        while entry is not None:
            parent, _ = entry
            if parent is None or isinstance(parent, (ast.FunctionDeclaration,
                                                     ast.Lambda)):
                break
            if isinstance(parent, ast.Block):
                pos = next((i for i, stmt in enumerate(parent.body)
                            if stmt is node), None)
                if pos is not None:
                    for stmt in parent.body[pos + 1:]:
                        alive = self._scan(stmt, decl, alive, usages,
                                           lambda_unsafe)
            if anchor is None and parent is self._parents.get(decl, (None,))[0]:
                # The declaring block has been scanned entirely.
                break
            node = parent
            entry = self._parents.get(node)
        if lambda_unsafe[0] and anchor is not None:
            # A mutable variable captured by a lambda loses flow refinement;
            # version-anchored demands are not reliable.
            return None
        return usages

    def _scan(self, node: ast.Node, decl: ast.VariableDeclaration,
              alive: bool, usages: list, lambda_unsafe: list) -> bool:
        name = decl.name
        if isinstance(node, ast.Variable):
            if node.name == name and alive:
                usages.append(node)
            return alive
        if isinstance(node, ast.Assignment) and node.receiver is None \
                and node.name == name:
            # The right-hand side still reads the tracked version.
            self._scan(node.expr, decl, alive, usages, lambda_unsafe)
            return False
        if isinstance(node, ast.VariableDeclaration) and node.name == name \
                and node is not decl:
            # Shadowing redeclaration: the initializer reads the tracked
            # version, but later usages refer to the new variable.
            self._scan(node.expr, decl, alive, usages, lambda_unsafe)
            return False
        if isinstance(node, ast.Block):
            for stmt in node.body:
                alive = self._scan(stmt, decl, alive, usages, lambda_unsafe)
            return alive
        if isinstance(node, ast.Conditional):
            self._scan(node.cond, decl, alive, usages, lambda_unsafe)
            alive_true = self._scan(node.true_branch, decl, alive, usages,
                                    lambda_unsafe)
            alive_false = self._scan(node.false_branch, decl, alive, usages,
                                     lambda_unsafe)
            return alive_true or alive_false
        if isinstance(node, ast.MultiConditional):
            if node.root_cond is not None:
                self._scan(node.root_cond, decl, alive, usages, lambda_unsafe)
            for cond in node.conditions:
                self._scan(cond, decl, alive, usages, lambda_unsafe)
            branch_alive = [self._scan(b, decl, alive, usages, lambda_unsafe)
                            for b in node.branches]
            has_else = len(node.conditions) == len(node.branches) - 1
            survives = any(branch_alive)
            if not has_else:
                # Some condition may be false: the original value survives.
                survives = survives or alive
            return survives
        if isinstance(node, ast.Loop):
            if node.cond is not None:
                self._scan(node.cond, decl, alive, usages, lambda_unsafe)
            self._scan(node.block, decl, alive, usages, lambda_unsafe)
            # Zero-iteration path: the loop never definitely reassigns.
            return alive
        if isinstance(node, ast.TryCatch):
            alive_try = self._scan(node.try_block, decl, alive, usages,
                                   lambda_unsafe)
            # A catch block may be entered before any statement of the try
            # block has run, so its entry state is the state before the try.
            alive_catches = [
                self._scan(block, decl, alive, usages, lambda_unsafe)
                for block in node.catch_blocks.values()
            ]
            return alive_try or any(alive_catches)
        if isinstance(node, (ast.Lambda, ast.FunctionDeclaration)):
            # Lambdas and nested functions capture the variable.
            if not decl.is_final:
                lambda_unsafe[0] = True
            if node.body is not None:
                # Captured usages are checked against the declaration version
                # (mutable captures disable flow refinement; finals never get
                # reassigned), so they are live for that version.
                self._scan(node.body, decl, True if not decl.is_final
                           else alive, usages, lambda_unsafe)
            return alive
        for child, _ in children_with_indices(node):
            if child is not None:
                self._scan(child, decl, alive, usages, lambda_unsafe)
        return alive

    def _most_specific(self, demands: List[Demand]) -> Optional[Demand]:
        best = None
        for demand in demands:
            if best is None:
                best = demand
                continue
            if demand.type == best.type:
                if demand.is_plain and not best.is_plain:
                    best = demand
                continue
            try:
                if demand.type.is_subtype(best.type):
                    best = demand
            except Exception:
                continue
        return best
