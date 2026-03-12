import networkx as nx

from src.ir import ast, types as tp, kotlin_types as kt
from src.ir.context import Context
# Pre-load generators package to avoid circular import.
from src.generators.api.builder import JavaAPIGraphBuilder  # noqa: F401
from src.generators.api import api_graph as ag, nodes
from src.enumerators.type_recovery import TypeHintRecovery
from src.enumerators.type_error import TypeErrorEnumerator


BT_FACTORY = kt.KotlinBuiltinFactory()


def mk_typed(expr, t):
    expr.mk_typed(ast.TypePair(expected=t, actual=t))
    return expr


def mk_const(t):
    return mk_typed(ast.BottomConstant(t), t)


def mk_var(name, t):
    return mk_typed(ast.Variable(name), t)


def mk_var_decl(name, expr, var_type=None, inferred_type=None,
                is_final=True):
    return ast.VariableDeclaration(name, expr, is_final=is_final,
                                   var_type=var_type,
                                   inferred_type=inferred_type or var_type)


def mk_main(stmts):
    return ast.FunctionDeclaration(
        "main", [], BT_FACTORY.get_void_type(), body=ast.Block(stmts),
        func_type=ast.FunctionDeclaration.FUNCTION, metadata={})


def mk_program(*decls):
    program = ast.Program(Context(), "kotlin")
    for decl in decls:
        program.add_declaration(decl)
    return program


def mk_empty_api_graph():
    return ag.APIGraph(nx.DiGraph(), nx.DiGraph(), {}, bt_factory=BT_FACTORY)


def mk_method(graph, name, param_types, ret_type, type_params):
    m = nodes.Method(name, None,
                     [nodes.Parameter(t, False) for t in param_types],
                     type_params, {})
    graph.add_node(m)
    kwargs = {}
    if ret_type.is_parameterized():
        kwargs["constraint"] = ret_type.get_type_variable_assignments()
        ret_type = ret_type.t_constructor
    graph.add_node(ret_type)
    graph.add_edge(m, ret_type, **kwargs)
    return m


def mk_call(name, args, type_args=None, ret_type=None, inferred=False):
    call = ast.FunctionCall(name, [ast.CallArgument(a) for a in args],
                            None, type_args or [])
    if ret_type is not None:
        mk_typed(call, ret_type)
    call.can_infer_type_args = inferred
    return call


def recovery_for(program, api_graph=None):
    return TypeHintRecovery(program, api_graph or mk_empty_api_graph(),
                            BT_FACTORY)


def hint_of(recovery, expr):
    """Returns the recovered demand's primary type (None if unconstrained)."""
    demand = recovery.recover_expected_type(expr)
    return None if demand is None else demand.type


def test_unconstrained_variable():
    # var x = [1]; var y = x; var z = y
    # No usage of x is checked against an explicit type: unconstrained.
    init = mk_const(kt.Integer)
    x = mk_var_decl("x", init, inferred_type=kt.Integer)
    y = mk_var_decl("y", mk_var("x", kt.Integer), inferred_type=kt.Integer)
    z = mk_var_decl("z", mk_var("y", kt.Integer), inferred_type=kt.Integer)
    program = mk_program(mk_main([x, y, z]))

    recovery = recovery_for(program)
    assert hint_of(recovery, init) is None
    # The program is not re-annotated.
    assert x.var_type is None and y.var_type is None and z.var_type is None


def test_pinned_by_later_annotated_decl():
    # var x = [1]; var y = x; var z: Int = y  ==> hint Int
    init = mk_const(kt.Integer)
    x = mk_var_decl("x", init, inferred_type=kt.Integer)
    y = mk_var_decl("y", mk_var("x", kt.Integer), inferred_type=kt.Integer)
    z = mk_var_decl("z", mk_var("y", kt.Integer), var_type=kt.Integer)
    program = mk_program(mk_main([x, y, z]))

    recovery = recovery_for(program)
    assert hint_of(recovery, init) == kt.Integer
    assert x.var_type is None and y.var_type is None


def test_most_specific_demand():
    # var x = [.]; var y: Number = x; var z: Int = x  ==> hint Int
    init = mk_const(kt.Integer)
    x = mk_var_decl("x", init, inferred_type=kt.Integer)
    y = mk_var_decl("y", mk_var("x", kt.Integer), var_type=kt.Number)
    z = mk_var_decl("z", mk_var("x", kt.Integer), var_type=kt.Integer)
    program = mk_program(mk_main([x, y, z]))

    recovery = recovery_for(program)
    assert hint_of(recovery, init) == kt.Integer


def test_polymorphic_call_chain_unconstrained():
    # fun id<T>(x: T): T = x
    # var x = [1]; var y = id(x); var z = y  ==> unconstrained
    type_param = tp.TypeParameter("T")
    graph = nx.DiGraph()
    m = mk_method(graph, "id", [type_param], type_param, [type_param])
    api_graph = ag.APIGraph(graph, nx.DiGraph(), {}, bt_factory=BT_FACTORY)

    init = mk_const(kt.Integer)
    x = mk_var_decl("x", init, inferred_type=kt.Integer)
    call = mk_call("id", [mk_var("x", kt.Integer)], ret_type=kt.Integer,
                   inferred=True)
    y = mk_var_decl("y", call, inferred_type=kt.Integer)
    z = mk_var_decl("z", mk_var("y", kt.Integer), inferred_type=kt.Integer)
    program = mk_program(mk_main([x, y, z]))

    recovery = recovery_for(program, api_graph)
    assert hint_of(recovery, init) is None


def test_polymorphic_call_chain_pinned():
    # fun id<T>(x: T): T = x
    # var x = [1]; var y = id(x); var z: Int = y  ==> hint Int
    type_param = tp.TypeParameter("T")
    graph = nx.DiGraph()
    mk_method(graph, "id", [type_param], type_param, [type_param])
    api_graph = ag.APIGraph(graph, nx.DiGraph(), {}, bt_factory=BT_FACTORY)

    init = mk_const(kt.Integer)
    x = mk_var_decl("x", init, inferred_type=kt.Integer)
    call = mk_call("id", [mk_var("x", kt.Integer)], ret_type=kt.Integer,
                   inferred=True)
    y = mk_var_decl("y", call, inferred_type=kt.Integer)
    z = mk_var_decl("z", mk_var("y", kt.Integer), var_type=kt.Integer)
    program = mk_program(mk_main([x, y, z]))

    recovery = recovery_for(program, api_graph)
    assert hint_of(recovery, init) == kt.Integer
    assert x.var_type is None and y.var_type is None


def test_call_with_explicit_type_args():
    # fun id<T>(x: T): T = x
    # var y = id<String>([x])  ==> the argument is demanded String
    type_param = tp.TypeParameter("T")
    graph = nx.DiGraph()
    mk_method(graph, "id", [type_param], type_param, [type_param])
    api_graph = ag.APIGraph(graph, nx.DiGraph(), {}, bt_factory=BT_FACTORY)

    arg = mk_var("x", kt.String)
    call = mk_call("id", [arg], type_args=[kt.String], ret_type=kt.String,
                   inferred=False)
    y = mk_var_decl("y", call, inferred_type=kt.String)
    program = mk_program(mk_main([y]))

    recovery = recovery_for(program, api_graph)
    assert hint_of(recovery, arg) == kt.String


def test_monomorphic_call_param_demand():
    # fun f(x: Number): Unit
    # f([x])  ==> the argument is demanded Number, even with no annotations
    graph = nx.DiGraph()
    mk_method(graph, "f", [kt.Number], BT_FACTORY.get_void_type(), [])
    api_graph = ag.APIGraph(graph, nx.DiGraph(), {}, bt_factory=BT_FACTORY)

    init = mk_const(kt.Integer)
    x = mk_var_decl("x", init, inferred_type=kt.Integer)
    call = mk_call("f", [mk_var("x", kt.Integer)],
                   ret_type=BT_FACTORY.get_void_type())
    program = mk_program(mk_main([x, call]))

    recovery = recovery_for(program, api_graph)
    assert hint_of(recovery, init) == kt.Number


def mk_assign(name, t):
    return ast.Assignment(name, mk_const(t))


def mk_stmt_conditional(true_stmts, false_stmts):
    return ast.Conditional(
        mk_const(kt.Boolean), ast.Block(true_stmts), ast.Block(false_stmts),
        BT_FACTORY.get_void_type(), is_expression=False)


def test_flow_refinement_reassigned_on_all_paths():
    # var x = [1]; if (...) { x = 2 } else { x = 4 }; var y: Int = x
    # The usage of x refers to another SSA version: unconstrained (Fig. 7a).
    init = mk_const(kt.Integer)
    x = mk_var_decl("x", init, inferred_type=kt.Integer, is_final=False)
    cond = mk_stmt_conditional([mk_assign("x", kt.Integer)],
                               [mk_assign("x", kt.Integer)])
    y = mk_var_decl("y", mk_var("x", kt.Integer), var_type=kt.Integer)
    program = mk_program(mk_main([x, cond, y]))

    recovery = recovery_for(program)
    assert hint_of(recovery, init) is None


def test_flow_refinement_clean_path_exists():
    # var x = [1]; if (...) { x = 2 } else { }; var y: Int = x
    # The else path does not reassign x: the usage pins it to Int (Fig. 7c).
    init = mk_const(kt.Integer)
    x = mk_var_decl("x", init, inferred_type=kt.Integer, is_final=False)
    cond = mk_stmt_conditional([mk_assign("x", kt.Integer)], [])
    y = mk_var_decl("y", mk_var("x", kt.Integer), var_type=kt.Integer)
    program = mk_program(mk_main([x, cond, y]))

    recovery = recovery_for(program)
    assert hint_of(recovery, init) == kt.Integer


def test_flow_refinement_straight_line_kill():
    # var x = [1]; x = 2; var y: Int = x  ==> unconstrained
    init = mk_const(kt.Integer)
    x = mk_var_decl("x", init, inferred_type=kt.Integer, is_final=False)
    assign = mk_assign("x", kt.Integer)
    y = mk_var_decl("y", mk_var("x", kt.Integer), var_type=kt.Integer)
    program = mk_program(mk_main([x, assign, y]))

    recovery = recovery_for(program)
    assert hint_of(recovery, init) is None


def test_flow_refinement_loop_zero_iterations():
    # var x = [1]; while (...) { x = 2 }; var y: Int = x
    # The loop may run zero times: the usage still pins x.
    init = mk_const(kt.Integer)
    x = mk_var_decl("x", init, inferred_type=kt.Integer, is_final=False)
    loop = ast.Loop(ast.Block([mk_assign("x", kt.Integer)]),
                    cond=mk_const(kt.Boolean))
    y = mk_var_decl("y", mk_var("x", kt.Integer), var_type=kt.Integer)
    program = mk_program(mk_main([x, loop, y]))

    recovery = recovery_for(program)
    assert hint_of(recovery, init) == kt.Integer


def test_assignment_version_demand():
    # var x = 1; x = [.]; var y: Int = x
    # The assignment introduces a version pinned by the later usage.
    x = mk_var_decl("x", mk_const(kt.Integer), inferred_type=kt.Integer,
                    is_final=False)
    rhs = mk_const(kt.Integer)
    assign = ast.Assignment("x", rhs)
    y = mk_var_decl("y", mk_var("x", kt.Integer), var_type=kt.Integer)
    program = mk_program(mk_main([x, assign, y]))

    recovery = recovery_for(program)
    assert hint_of(recovery, rhs) == kt.Integer


def test_assignment_conforms_to_declared_type():
    # var x = 1; x = [.]
    # No later usage, but Kotlin assignments must conform to the type
    # inferred at the declaration (whose initializer is unchanged).
    x = mk_var_decl("x", mk_const(kt.Integer), inferred_type=kt.Integer,
                    is_final=False)
    rhs = mk_const(kt.Integer)
    assign = ast.Assignment("x", rhs)
    program = mk_program(mk_main([x, assign]))

    recovery = recovery_for(program)
    assert hint_of(recovery, rhs) == kt.Integer


def test_assignment_to_annotated_variable():
    # var x: Number = 1; x = [.]  ==> demanded Number
    x = mk_var_decl("x", mk_const(kt.Integer), var_type=kt.Number,
                    is_final=False)
    rhs = mk_const(kt.Integer)
    assign = ast.Assignment("x", rhs)
    program = mk_program(mk_main([x, assign]))

    recovery = recovery_for(program)
    assert hint_of(recovery, rhs) == kt.Number


def test_overloaded_call_demand_requires_consensus():
    # fun f(x: String): Unit  |  fun f(x: Number): Unit
    # f([x])  ==> overloads disagree on the parameter type: the replacement
    # could be rescued by overload switching, so no demand.
    graph = nx.DiGraph()
    mk_method(graph, "f", [kt.String], BT_FACTORY.get_void_type(), [])
    mk_method(graph, "f", [kt.Number], BT_FACTORY.get_void_type(), [])
    api_graph = ag.APIGraph(graph, nx.DiGraph(), {}, bt_factory=BT_FACTORY)

    init = mk_const(kt.String)
    x = mk_var_decl("x", init, inferred_type=kt.String)
    call = mk_call("f", [mk_var("x", kt.String)],
                   ret_type=BT_FACTORY.get_void_type())
    program = mk_program(mk_main([x, call]))

    recovery = recovery_for(program, api_graph)
    assert hint_of(recovery, init) is None


def test_field_assignment_demand():
    # class A { var f: Number }  with  f = [.]  inside a method of A:
    # the assignment is checked against the declared field type.
    field = ast.FieldDeclaration("f", kt.Number, is_final=False)
    rhs = mk_const(kt.Integer)
    assign = ast.Assignment("f", rhs)
    method = ast.FunctionDeclaration(
        "m", [], BT_FACTORY.get_void_type(), body=ast.Block([assign]),
        func_type=ast.FunctionDeclaration.CLASS_METHOD, metadata={})
    cls = ast.ClassDeclaration("A", [], fields=[field], functions=[method])
    program = mk_program(cls)

    recovery = recovery_for(program)
    assert hint_of(recovery, rhs) == kt.Number


def test_receiver_field_assignment_fallback():
    # a.f = [.] where the receiver carries no type information: the field is
    # resolved by its (unique) name against the program's classes.
    field = ast.FieldDeclaration("f", kt.Number, is_final=False)
    cls = ast.ClassDeclaration("A", [], fields=[field], functions=[])
    rhs = mk_const(kt.Integer)
    assign = ast.Assignment("f", rhs, receiver=ast.Variable("a"))
    program = mk_program(cls, mk_main([assign]))

    recovery = recovery_for(program)
    assert hint_of(recovery, rhs) == kt.Number


def test_conditional_branch_passthrough():
    # var y: Number = if (c) [x] else 2  ==> branch demanded Number
    branch = mk_var("x", kt.Integer)
    conditional = ast.Conditional(mk_const(kt.Boolean), branch,
                                  mk_const(kt.Integer), kt.Number,
                                  is_expression=True)
    y = mk_var_decl("y", conditional, var_type=kt.Number)
    program = mk_program(mk_main([y]))

    recovery = recovery_for(program)
    assert hint_of(recovery, branch) == kt.Number


def test_conditional_condition_demands_bool():
    cond = mk_var("c", kt.Boolean)
    conditional = ast.Conditional(cond, mk_const(kt.Integer),
                                  mk_const(kt.Integer), kt.Integer,
                                  is_expression=True)
    y = mk_var_decl("y", conditional, var_type=kt.Integer)
    program = mk_program(mk_main([y]))

    recovery = recovery_for(program)
    assert hint_of(recovery, cond) == BT_FACTORY.get_boolean_type()


def test_function_return_demand():
    # fun f(): Number = [x]  ==> demanded Number
    body = mk_var("x", kt.Integer)
    func = ast.FunctionDeclaration(
        "f", [], kt.Number, body=body,
        func_type=ast.FunctionDeclaration.FUNCTION, metadata={})
    program = mk_program(func)

    recovery = recovery_for(program)
    assert hint_of(recovery, body) == kt.Number


def test_statement_position_has_no_demand():
    # A statement-position expression constrains nothing.
    stmt = mk_const(kt.Integer)
    program = mk_program(mk_main([stmt, mk_const(kt.Integer)]))

    recovery = recovery_for(program)
    assert hint_of(recovery, stmt) is None


def test_logical_operand_demands_bool():
    # var x = [true]; if (x && c) ... ==> demanded Boolean (Kotlin)
    init = mk_const(kt.Boolean)
    x = mk_var_decl("x", init, inferred_type=kt.Boolean)
    logical = ast.LogicalExpr(mk_var("x", kt.Boolean),
                              mk_const(kt.Boolean), ast.Operator("&&"))
    cond = ast.Conditional(logical, mk_const(kt.Integer),
                           mk_const(kt.Integer), kt.Integer,
                           is_expression=True)
    y = mk_var_decl("y", cond, var_type=kt.Integer)
    program = mk_program(mk_main([x, y]))

    recovery = recovery_for(program)
    assert hint_of(recovery, init) == BT_FACTORY.get_boolean_type()


def test_comparison_operand_numeric_demand():
    # var x = [1]; x > 5  ==> numeric demand: String breaks, Long does not
    init = mk_const(kt.Integer)
    x = mk_var_decl("x", init, inferred_type=kt.Integer)
    cmp_expr = ast.ComparisonExpr(mk_var("x", kt.Integer),
                                  mk_const(kt.Integer), ast.Operator(">"))
    y = mk_var_decl("y", cmp_expr, var_type=kt.Boolean)
    program = mk_program(mk_main([x, y]))

    recovery = recovery_for(program)
    demand = recovery.recover_expected_type(init)
    assert demand is not None
    assert demand.type == kt.Number
    assert demand.allows(kt.String)
    assert not demand.allows(kt.Long)
    assert not demand.allows(kt.Integer)


def test_arith_plus_left_string_rescued():
    # Kotlin: "s" + 5 is valid string concatenation, so a String candidate
    # on the left of + must not be enumerated.
    init = mk_const(kt.Integer)
    x = mk_var_decl("x", init, inferred_type=kt.Integer)
    arith = ast.ArithExpr(mk_var("x", kt.Integer), mk_const(kt.Integer),
                          ast.Operator("+"))
    y = mk_var_decl("y", arith, var_type=kt.Integer)
    program = mk_program(mk_main([x, y]))

    recovery = recovery_for(program)
    demand = recovery.recover_expected_type(init)
    assert demand is not None
    assert not demand.allows(kt.String)
    assert demand.allows(BT_FACTORY.get_any_type())


def test_equality_operand_unconstrained():
    init = mk_const(kt.Integer)
    x = mk_var_decl("x", init, inferred_type=kt.Integer)
    eq = ast.EqualityExpr(mk_var("x", kt.Integer), mk_const(kt.Integer),
                          ast.Operator("=="))
    y = mk_var_decl("y", eq, var_type=kt.Boolean)
    program = mk_program(mk_main([x, y]))

    recovery = recovery_for(program)
    assert hint_of(recovery, init) is None


def test_elvis_right_passthrough_and_left_base_type():
    # var y: Number = (a ?: x): the right operand inherits the demand; a
    # nullable Number on the left is rescued (its base type satisfies it).
    init = mk_const(kt.Integer)
    x = mk_var_decl("x", init, inferred_type=kt.Integer)
    elvis = ast.BinaryExpr(mk_var("x", kt.Integer), mk_const(kt.Integer),
                           ast.Operator("?:"))
    y = mk_var_decl("y", elvis, var_type=kt.Number)
    program = mk_program(mk_main([x, y]))

    recovery = recovery_for(program)
    demand = recovery.recover_expected_type(init)
    assert demand is not None and demand.type == kt.Number
    assert demand.allows(kt.String)
    assert not demand.allows(tp.NullableType().new([kt.Integer]))


def test_when_subject_unconstrained():
    # when (x) { v -> ... }: subject and case values match by equality.
    init = mk_const(kt.Integer)
    x = mk_var_decl("x", init, inferred_type=kt.Integer)
    subject = mk_var("x", kt.Integer)
    when = ast.MultiConditional(
        [mk_const(kt.Integer)], [mk_const(kt.String), mk_const(kt.String)],
        kt.String, root_cond=subject, is_expression=True)
    y = mk_var_decl("y", when, var_type=kt.String)
    program = mk_program(mk_main([x, y]))

    recovery = recovery_for(program)
    assert hint_of(recovery, init) is None


def mk_class_type_with_method(graph, cls_name, method_name, param_types,
                              ret_type):
    cls_t = tp.SimpleClassifier(cls_name, [BT_FACTORY.get_any_type()])
    graph.add_node(cls_t)
    m = nodes.Method(method_name, cls_name,
                     [nodes.Parameter(t, False) for t in param_types],
                     [], {})
    graph.add_node(m)
    graph.add_edge(cls_t, m)
    graph.add_node(ret_type)
    graph.add_edge(m, ret_type)
    return cls_t, m


def test_receiver_usage_member_demand():
    # var x = [A()]; x.m(5): a candidate without a compatible m breaks the
    # lookup; a candidate defining m(Int) is rescued.
    graph = nx.DiGraph()
    a_t, _ = mk_class_type_with_method(graph, "A", "m", [kt.Integer],
                                       BT_FACTORY.get_void_type())
    b_t, _ = mk_class_type_with_method(graph, "B", "m", [kt.Integer],
                                       BT_FACTORY.get_void_type())
    c_t, _ = mk_class_type_with_method(graph, "C", "other", [kt.Integer],
                                       BT_FACTORY.get_void_type())
    api_graph = ag.APIGraph(graph, nx.DiGraph(), {}, bt_factory=BT_FACTORY)

    init = mk_const(a_t)
    x = mk_var_decl("x", init, inferred_type=a_t)
    receiver = mk_var("x", a_t)
    call = ast.FunctionCall("m", [ast.CallArgument(mk_const(kt.Integer))],
                            receiver=receiver)
    mk_typed(call, BT_FACTORY.get_void_type())
    program = mk_program(mk_main([x, call]))

    recovery = recovery_for(program, api_graph)
    demand = recovery.recover_expected_type(init)
    assert demand is not None and demand.kind == "member"
    assert demand.type == a_t
    assert not demand.allows(b_t)   # B defines a compatible m
    assert demand.allows(c_t)       # C does not define m
    assert demand.allows(kt.String)


def test_receiver_extension_member_guarded():
    # x.toString() exists on every type: no demand.
    graph = nx.DiGraph()
    a_t, _ = mk_class_type_with_method(graph, "A", "toString", [],
                                       kt.String)
    api_graph = ag.APIGraph(graph, nx.DiGraph(), {}, bt_factory=BT_FACTORY)

    init = mk_const(a_t)
    x = mk_var_decl("x", init, inferred_type=a_t)
    call = ast.FunctionCall("toString", [], receiver=mk_var("x", a_t))
    mk_typed(call, kt.String)
    program = mk_program(mk_main([x, call]))

    recovery = recovery_for(program, api_graph)
    assert hint_of(recovery, init) is None


def mk_query_constructor():
    type_param = tp.TypeParameter("R")
    return tp.TypeConstructor("TemporalQuery", [type_param]), type_param


def test_generic_param_invariance_forcing():
    # <R> R query(TemporalQuery<R> q) with omitted type args, result pinned
    # to Int: candidates break unless their TemporalQuery instantiation is a
    # subtype of Int.
    q_con, r_param = mk_query_constructor()
    method_tvar = tp.TypeParameter("R")
    graph = nx.DiGraph()
    m = nodes.Method("query", None,
                     [nodes.Parameter(q_con.new([method_tvar]), False)],
                     [method_tvar], {})
    graph.add_node(m)
    graph.add_node(method_tvar)
    graph.add_edge(m, method_tvar)
    api_graph = ag.APIGraph(graph, nx.DiGraph(), {}, bt_factory=BT_FACTORY)

    init = mk_const(q_con.new([kt.Integer]))
    x = mk_var_decl("x", init, inferred_type=q_con.new([kt.Integer]))
    call = mk_call("query", [mk_var("x", q_con.new([kt.Integer]))],
                   ret_type=kt.Integer, inferred=True)
    y = mk_var_decl("y", call, var_type=kt.Integer)
    program = mk_program(mk_main([x, y]))

    recovery = recovery_for(program, api_graph)
    demand = recovery.recover_expected_type(init)
    assert demand is not None and demand.kind == "ctor-arg"
    assert demand.type == q_con.new([kt.Integer])
    # TemporalQuery<String>: R forced to String, violates R <: Int.
    assert demand.allows(q_con.new([kt.String]))
    # A type with no TemporalQuery supertype can never satisfy the call.
    assert demand.allows(kt.String)
    # TemporalQuery<Int> satisfies the call: rescued.
    assert not demand.allows(q_con.new([kt.Integer]))
    # TemporalQuery<Nothing>-style instantiations satisfy R <: Int: rescued.
    assert not demand.allows(q_con.new([tp.Nothing]))


def test_bare_tvar_declared_bound():
    # <T : Number> T id(T x) with omitted type args and unconstrained
    # result: the declared bound still pins the argument.
    bound_tvar = tp.TypeParameter("T", bound=kt.Number)
    graph = nx.DiGraph()
    m = nodes.Method("id", None, [nodes.Parameter(bound_tvar, False)],
                     [bound_tvar], {})
    graph.add_node(m)
    graph.add_node(bound_tvar)
    graph.add_edge(m, bound_tvar)
    api_graph = ag.APIGraph(graph, nx.DiGraph(), {}, bt_factory=BT_FACTORY)

    init = mk_const(kt.Integer)
    x = mk_var_decl("x", init, inferred_type=kt.Integer)
    call = mk_call("id", [mk_var("x", kt.Integer)], ret_type=kt.Integer,
                   inferred=True)
    y = mk_var_decl("y", call, inferred_type=kt.Integer)
    program = mk_program(mk_main([x, y]))

    recovery = recovery_for(program, api_graph)
    assert hint_of(recovery, init) == kt.Number


def mk_entry_graph(ret_constructor=None):
    """
    <K, V> Entry<K, V> entry(K, V)   plus   Entry<K2, V2>.getValue(): V2
    (or getValue(): ret_constructor<V2> when given).
    """
    k2 = tp.TypeParameter("K2")
    v2 = tp.TypeParameter("V2")
    entry_con = tp.TypeConstructor("Entry", [k2, v2],
                                   [BT_FACTORY.get_any_type()])
    k = tp.TypeParameter("K")
    v = tp.TypeParameter("V")
    graph = nx.DiGraph()
    entry_m = nodes.Method("entry", None,
                           [nodes.Parameter(k, False),
                            nodes.Parameter(v, False)], [k, v], {})
    graph.add_node(entry_m)
    graph.add_node(entry_con)
    graph.add_edge(entry_m, entry_con,
                   constraint={k2: k, v2: v})
    ret_t = v2 if ret_constructor is None else ret_constructor.new([v2])
    get_m = nodes.Method("getValue", "Entry", [], [], {})
    graph.add_node(get_m)
    if ret_t.is_parameterized():
        graph.add_node(ret_t.t_constructor)
        graph.add_edge(get_m, ret_t.t_constructor,
                       constraint=ret_t.get_type_variable_assignments())
    else:
        graph.add_node(ret_t)
        graph.add_edge(get_m, ret_t)
    graph.add_edge(entry_con, get_m)
    subtyping = nx.DiGraph()
    subtyping.add_node(entry_con)
    api = ag.APIGraph(graph, subtyping, {}, bt_factory=BT_FACTORY)
    return api, entry_con


def test_demand_threads_through_member_result():
    # var x = [.]; Integer y = entry(k, x).getValue()
    # The demand on getValue()'s result pins V through the member access.
    api, entry_con = mk_entry_graph()
    init = mk_const(kt.Integer)
    x = mk_var_decl("x", init, inferred_type=kt.Integer)
    call = mk_call("entry", [mk_const(kt.String), mk_var("x", kt.Integer)],
                   inferred=True)
    mk_typed(call, entry_con.new([kt.String, kt.Integer]))
    access = ast.FunctionCall("getValue", [], receiver=call)
    mk_typed(access, kt.Integer)
    y = mk_var_decl("y", access, var_type=kt.Integer)
    program = mk_program(mk_main([x, y]))

    recovery = recovery_for(program, api)
    assert hint_of(recovery, init) == kt.Integer


def test_member_result_threading_unconstrained():
    # var y = entry(k, x).getValue() with y unannotated: still no demand.
    api, entry_con = mk_entry_graph()
    init = mk_const(kt.Integer)
    x = mk_var_decl("x", init, inferred_type=kt.Integer)
    call = mk_call("entry", [mk_const(kt.String), mk_var("x", kt.Integer)],
                   inferred=True)
    mk_typed(call, entry_con.new([kt.String, kt.Integer]))
    access = ast.FunctionCall("getValue", [], receiver=call)
    mk_typed(access, kt.Integer)
    y = mk_var_decl("y", access, inferred_type=kt.Integer)
    program = mk_program(mk_main([x, y]))

    recovery = recovery_for(program, api)
    assert hint_of(recovery, init) is None


def test_member_result_contravariant_guard():
    # getValue(): Comparator<V2> (V2 contravariant in spirit): a derived
    # bound would be a lower bound, so no demand may be produced.
    comp_con = tp.TypeConstructor(
        "Comparator", [tp.TypeParameter("T", tp.Contravariant)],
        [BT_FACTORY.get_any_type()])
    api, entry_con = mk_entry_graph(ret_constructor=comp_con)
    init = mk_const(kt.Integer)
    x = mk_var_decl("x", init, inferred_type=kt.Integer)
    call = mk_call("entry", [mk_const(kt.String), mk_var("x", kt.Integer)],
                   inferred=True)
    mk_typed(call, entry_con.new([kt.String, kt.Integer]))
    access = ast.FunctionCall("getValue", [], receiver=call)
    mk_typed(access, comp_con.new([kt.Integer]))
    y = mk_var_decl("y", access, var_type=comp_con.new([kt.Integer]))
    program = mk_program(mk_main([x, y]))

    recovery = recovery_for(program, api)
    assert hint_of(recovery, init) is None


def test_named_argument_demand():
    # fun f(a: Int, b: String): named argument b matched by name.
    func = ast.FunctionDeclaration(
        "f",
        [ast.ParameterDeclaration("a", kt.Integer),
         ast.ParameterDeclaration("b", kt.String)],
        BT_FACTORY.get_void_type(), body=ast.Block([]),
        func_type=ast.FunctionDeclaration.FUNCTION, metadata={})
    init = mk_const(kt.String)
    x = mk_var_decl("x", init, inferred_type=kt.String)
    arg = mk_var("x", kt.String)
    call = ast.FunctionCall(
        "f", [ast.CallArgument(mk_const(kt.Integer)),
              ast.CallArgument(arg, name="b")])
    mk_typed(call, BT_FACTORY.get_void_type())
    program = mk_program(func, mk_main([x, call]))

    recovery = recovery_for(program)
    assert hint_of(recovery, init) == kt.String


def make_enumerator_stub():
    enum = object.__new__(TypeErrorEnumerator)
    enum.bt_factory = BT_FACTORY
    enum.options = {}
    return enum


def test_usable_hints():
    enum = make_enumerator_stub()
    assert not enum.is_usable_hint(None)
    assert not enum.is_usable_hint(BT_FACTORY.get_any_type())
    assert not enum.is_usable_hint(
        tp.NullableType().new([BT_FACTORY.get_any_type()]))
    assert not enum.is_usable_hint(BT_FACTORY.get_void_type())
    assert enum.is_usable_hint(kt.Integer)
    assert enum.is_usable_hint(kt.Number)
    assert enum.is_usable_hint(
        BT_FACTORY.get_array_type().new([kt.Integer]))
