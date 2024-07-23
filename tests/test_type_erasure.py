import networkx as nx

from src.ir import types as tp, ast, kotlin_types as kt
from src.generators.api import api_graph as ag, nodes, type_erasure as te


def mk_method(graph, name, param_types, ret_type, type_params,
              receiver=None):
    cls_name = None if not receiver else receiver.name
    m = nodes.Method(name, cls_name,
                     [nodes.Parameter(t, False) for t in param_types],
                     type_params, {})
    graph.add_node(m)
    kwargs = {}
    if ret_type.is_parameterized():
        kwargs["constraint"] = ret_type.get_type_variable_assignments()
        ret_type = ret_type.t_constructor
    graph.add_node(ret_type)
    graph.add_edge(m, ret_type, **kwargs)
    if receiver:
        graph.add_edge(receiver, m)
    return m


def mk_field(graph, name, field_type, receiver=None):
    cls_name = None if not receiver else receiver.name
    f = nodes.Field(name, cls_name, {})
    graph.add_node(f)
    kwargs = {}
    if field_type.is_parameterized():
        kwargs["constraint"] = field_type.get_type_variable_assignments()
        field_type = field_type.t_constructor
    graph.add_node(field_type)
    graph.add_edge(f, field_type, **kwargs)
    if receiver:
        graph.add_edge(receiver, f)
    return f


def mk_method_call(name, arg_types, type_args, ret_type, expected_type=None):
    args = []
    for t in arg_types:
        arg = ast.BottomConstant(t)
        arg.mk_typed(ast.TypePair(actual=t, expected=t))
        arg = ast.CallArgument(arg)
        args.append(arg)

    call = ast.FunctionCall(name, args, None, type_args)
    call.mk_typed(ast.TypePair(expected=expected_type, actual=ret_type))
    return call


def mk_var_decl(var_type, var_name="x"):
    return ast.VariableDeclaration(var_name, var_type=var_type,
                                   expr=ast.BottomConstant(var_type))


def mk_assign(t):
    return ast.Assignment("x", ast.BottomConstant(t))


def mk_block(t, body=None):
    return ast.Block(body or [ast.BottomConstant(t)])


def mk_expr(t):
    expr = ast.BottomConstant(t)
    expr.mk_typed(ast.TypePair(expected=t, actual=t))
    return expr


def mk_func_decl(t, body=None):
    body = body or ast.BottomConstant(t)
    return ast.FunctionDeclaration("x", [], t, body=body,
                                   func_type=ast.FunctionDeclaration.FUNCTION)


def test_erase_types_illtyped():
    bt_factory = kt.KotlinBuiltinFactory()
    type_param = tp.TypeParameter("T")

    # Case 1:
    # <T> void m(T x)
    # m<String>("") -> m(1): Valid: Not erase types
    graph = nx.DiGraph()
    m = mk_method(graph, "foo", [type_param], bt_factory.get_void_type(),
                  [type_param])
    api_graph = ag.APIGraph(graph, nx.DiGraph(), {},
                            bt_factory=bt_factory)
    type_eraser = te.TypeEraser(api_graph, bt_factory, False)

    func_call = mk_method_call("foo", [kt.String], [kt.String],
                               bt_factory.get_void_type())

    assert not func_call.can_infer_type_args
    type_eraser.erase_types_ill_typed(func_call, m, kt.Number, 0, [])
    assert not func_call.can_infer_type_args

    # Case 2
    # <T> void m(A<T> x)
    # m<String>(A<String>()) -> m(A<Number>()): Not erase types
    graph = nx.DiGraph()
    a = tp.TypeConstructor("A", [type_param])
    m = mk_method(graph, "foo", [a.new([type_param])],
                  bt_factory.get_void_type(), [type_param])
    type_eraser.api_graph = ag.APIGraph(graph, nx.DiGraph(), {},
                                        bt_factory=bt_factory)
    func_call = mk_method_call("foo", [a.new([kt.String])], [kt.String],
                               bt_factory.get_void_type())
    assert not func_call.can_infer_type_args
    type_eraser.erase_types_ill_typed(func_call, m,
                                      a.new([kt.Number]), 0, [])
    assert not func_call.can_infer_type_args

    # Case 3
    # <T> void m(A<T> x)
    # m<String>(A<String>()) -> m(B()): Erase types
    assert not func_call.can_infer_type_args
    type_eraser.erase_types_ill_typed(func_call, m,
                                      tp.SimpleClassifier("B"), 0, [])
    assert func_call.can_infer_type_args


def test_erase_types_illtyped_mul_args():
    bt_factory = kt.KotlinBuiltinFactory()
    type_param = tp.TypeParameter("T")

    # Case 1:
    # <T> void m(T x, int y)
    # m<String>("", 1) -> m(1, 1): Valid: Not erase types
    graph = nx.DiGraph()
    m = mk_method(graph, "foo", [type_param, kt.Integer],
                  bt_factory.get_void_type(), [type_param])
    api_graph = ag.APIGraph(graph, nx.DiGraph(), {},
                            bt_factory=bt_factory)
    type_eraser = te.TypeEraser(api_graph, bt_factory, False)

    func_call = mk_method_call("foo", [kt.String, kt.Integer], [kt.String],
                               bt_factory.get_void_type())

    assert not func_call.can_infer_type_args
    type_eraser.erase_types_ill_typed(func_call, m, kt.Number, 0, [])
    assert not func_call.can_infer_type_args

    # Case 2
    # <T> void m(T x, T y)
    # m<String>("", "") -> m(1, ""): Valid Not erase types
    graph = nx.DiGraph()
    m = mk_method(graph, "foo", [type_param, type_param],
                  bt_factory.get_void_type(), [type_param])
    api_graph = ag.APIGraph(graph, nx.DiGraph(), {},
                            bt_factory=bt_factory)
    type_eraser = te.TypeEraser(api_graph, bt_factory, False)

    func_call = mk_method_call("foo", [kt.String, kt.String], [kt.String],
                               bt_factory.get_void_type(), [])

    assert not func_call.can_infer_type_args
    type_eraser.erase_types_ill_typed(func_call, m, kt.Number, 0, [])
    assert not func_call.can_infer_type_args

    # Case 3
    # <T> void m(T x, A<T> y)
    # m<String>("", A<String>()) -> m(1, A<String>()): Erase types
    a = tp.TypeConstructor("A", [type_param])
    graph = nx.DiGraph()
    m = mk_method(graph, "foo", [type_param, a.new([type_param])],
                  bt_factory.get_void_type(), [type_param])
    api_graph = ag.APIGraph(graph, nx.DiGraph(), {},
                            bt_factory=bt_factory)
    type_eraser = te.TypeEraser(api_graph, bt_factory, False)

    func_call = mk_method_call("foo", [kt.String, a.new([kt.String])],
                               [kt.String], bt_factory.get_void_type())

    assert not func_call.can_infer_type_args
    type_eraser.erase_types_ill_typed(func_call, m, kt.Number, 0, [])
    assert func_call.can_infer_type_args

    # Case 4
    # <T> void m(T x, A<? extends T> y)
    # m<String>("", A<String>()) -> m(1, A<String>()): Not Erase types
    a = tp.TypeConstructor("A", [type_param])
    graph = nx.DiGraph()
    m = mk_method(graph, "foo",
                  [type_param, a.new([tp.WildCardType(type_param, tp.Covariant)])],
                  bt_factory.get_void_type(), [type_param])
    api_graph = ag.APIGraph(graph, nx.DiGraph(), {},
                            bt_factory=bt_factory)
    type_eraser = te.TypeEraser(api_graph, bt_factory, False)

    func_call = mk_method_call("foo", [kt.String, a.new([kt.String])],
                               [kt.String], bt_factory.get_void_type())

    assert not func_call.can_infer_type_args
    type_eraser.erase_types_ill_typed(func_call, m, kt.Number, 0, [])
    assert not func_call.can_infer_type_args


def test_erase_types_illtyped_ret_type():
    bt_factory = kt.KotlinBuiltinFactory()
    type_param = tp.TypeParameter("T")

    # Case 1:
    # <T> T m(T x)
    # String x = m<String>(1) -> String x = m(1): Erase types
    graph = nx.DiGraph()
    m = mk_method(graph, "foo", [type_param],
                  type_param, [type_param])
    api_graph = ag.APIGraph(graph, nx.DiGraph(), {},
                            bt_factory=bt_factory)
    type_eraser = te.TypeEraser(api_graph, bt_factory, False)

    func_call = mk_method_call("foo", [kt.String], [kt.String],
                               kt.String, kt.String)

    var_decl = mk_var_decl(kt.String)
    assert not func_call.can_infer_type_args
    type_eraser.erase_types_ill_typed(func_call, m, kt.Number, 0,
                                      [(var_decl, 0)])
    assert func_call.can_infer_type_args or var_decl.var_type is None

    # Case 2:
    # <T> T m()
    # m<String>("") -> m(1): Not erase types
    func_call = mk_method_call("foo", [kt.String], [kt.String],
                               kt.String, None)

    assert not func_call.can_infer_type_args
    type_eraser.erase_types_ill_typed(func_call, m, kt.Number, 0, [])
    assert not func_call.can_infer_type_args

    # Case 3:
    # <T, Y> A<X, Y> m(T x, Y x)
    # A<String, Integer> x = m<String, Integer>("", 1) ->
    # A<String, Integer> x = m(1, 1): Erase types
    type_param2 = tp.TypeParameter("Y")
    a = tp.TypeConstructor("A", [type_param, type_param2])
    graph = nx.DiGraph()
    m = mk_method(graph, "foo", [type_param, type_param2],
                  a.new([type_param, type_param2]), [type_param, type_param2])
    api_graph = ag.APIGraph(graph, nx.DiGraph(), {},
                            bt_factory=bt_factory)
    type_eraser = te.TypeEraser(api_graph, bt_factory, False)

    ret_type = a.new([kt.String, kt.Integer])
    func_call = mk_method_call("foo", [kt.String, kt.Integer],
                               [kt.String, kt.Integer],
                               ret_type, ret_type)
    var_decl = mk_var_decl(ret_type)
    assert not func_call.can_infer_type_args
    type_eraser.erase_types_ill_typed(func_call, m, kt.Number, 0,
                                      [(var_decl, 0)])
    assert func_call.can_infer_type_args or var_decl.var_type is None

    # Case 4: The same as Case 3, but the expected type is None
    func_call = mk_method_call("foo", [kt.String, kt.Integer],
                               [kt.String, kt.Integer],
                               ret_type, None)
    assert not func_call.can_infer_type_args
    type_eraser.erase_types_ill_typed(func_call, m, kt.Number, 0, [])
    assert not func_call.can_infer_type_args

    # Case 5:
    # <T : Number> T m(T x)
    # Integer x = m<Integer>(1) -> Integer x = m("") : Erase types
    type_param = tp.TypeParameter("T", bound=kt.Number)
    graph = nx.DiGraph()
    m = mk_method(graph, "foo", [type_param],
                  type_param, [type_param])
    api_graph = ag.APIGraph(graph, nx.DiGraph(), {},
                            bt_factory=bt_factory)
    type_eraser = te.TypeEraser(api_graph, bt_factory, False)

    func_call = mk_method_call("foo", [kt.Integer], [kt.Integer],
                               kt.Integer, kt.Integer)

    var_decl = mk_var_decl(kt.Integer)
    assert not func_call.can_infer_type_args
    type_eraser.erase_types_ill_typed(func_call, m, kt.Number, 0,
                                      [(var_decl, 0)])
    assert func_call.can_infer_type_args or var_decl.var_type is None

    # Case 6: Same as case 5, but this time the expected type is None
    func_call = mk_method_call("foo", [kt.Integer], [kt.Integer],
                               kt.Integer, None)

    assert not func_call.can_infer_type_args
    type_eraser.erase_types_ill_typed(func_call, m, kt.Number, 0, [])
    assert not func_call.can_infer_type_args

    assert not func_call.can_infer_type_args
    type_eraser.erase_types_ill_typed(func_call, m, kt.String, 0, [])
    assert func_call.can_infer_type_args


def test_erase_types_illtyped_ret_type_assign():
    bt_factory = kt.KotlinBuiltinFactory()
    type_param = tp.TypeParameter("T")

    # Case 1:
    # <T> T m(T x)
    # x.f = m<String>(1) -> x.f = m(1): Erase types
    graph = nx.DiGraph()
    m = mk_method(graph, "foo", [type_param],
                  type_param, [type_param])
    api_graph = ag.APIGraph(graph, nx.DiGraph(), {},
                            bt_factory=bt_factory)
    type_eraser = te.TypeEraser(api_graph, bt_factory, False)

    func_call = mk_method_call("foo", [kt.String], [kt.String],
                               kt.String, kt.String)

    assign = mk_assign(kt.String)
    assert not func_call.can_infer_type_args
    type_eraser.erase_types_ill_typed(func_call, m, kt.Number, 0,
                                      [(assign, 0)])
    assert func_call.can_infer_type_args


def test_erase_types_illtyped_ret_type_func_decl():
    bt_factory = kt.KotlinBuiltinFactory()
    type_param = tp.TypeParameter("T")

    # Case 1:
    # <T> T m(T x)
    # x.f = m<String>(1) -> x.f = m(1): Erase types
    graph = nx.DiGraph()
    m = mk_method(graph, "foo", [type_param],
                  type_param, [type_param])
    api_graph = ag.APIGraph(graph, nx.DiGraph(), {},
                            bt_factory=bt_factory)
    type_eraser = te.TypeEraser(api_graph, bt_factory, False)

    func_call = mk_method_call("foo", [kt.String], [kt.String],
                               kt.String, kt.String)

    func_decl = mk_func_decl(kt.String)
    assert not func_call.can_infer_type_args
    type_eraser.erase_types_ill_typed(func_call, m, kt.Number, 0,
                                      [(func_decl, 0)])
    assert func_call.can_infer_type_args

    func_call = mk_method_call("foo", [kt.String], [kt.String],
                               kt.String, kt.String)
    block = mk_block(kt.String, [func_call])
    func_decl = mk_func_decl(kt.String, block)
    assert not func_call.can_infer_type_args
    type_eraser.erase_types_ill_typed(func_call, m, kt.Number, 0,
                                      [(block, 0),
                                       (func_decl, 0)])
    assert func_call.can_infer_type_args


def test_erase_types_illtyped_binary_expr():
    bt_factory = kt.KotlinBuiltinFactory()
    type_param = tp.TypeParameter("T")

    # Case 1:
    # <T> T m(T x)
    # m<String>(1) == "" -> m(1) == "": Not erase types
    graph = nx.DiGraph()
    m = mk_method(graph, "foo", [type_param],
                  type_param, [type_param])
    api_graph = ag.APIGraph(graph, nx.DiGraph(), {},
                            bt_factory=bt_factory)
    type_eraser = te.TypeEraser(api_graph, bt_factory, False)

    func_call = mk_method_call("foo", [kt.String], [kt.String],
                               kt.String, kt.String)

    binary_expr = ast.EqualityExpr(func_call, ast.BottomConstant(kt.String),
                                   ast.EqualityExpr.ALL_OPERATORS[0])
    assert not func_call.can_infer_type_args
    type_eraser.erase_types_ill_typed(func_call, m, kt.Number, 0,
                                      [(binary_expr, 0)])
    assert not func_call.can_infer_type_args


def test_erase_types_illtyped_conditional():
    bt_factory = kt.KotlinBuiltinFactory()
    type_param = tp.TypeParameter("T")

    # Case 1:
    # <T> T m(T x)
    # if(m<Boolean>(false)) -> if(m(1)): erase types
    graph = nx.DiGraph()
    m = mk_method(graph, "foo", [type_param],
                  type_param, [type_param])
    api_graph = ag.APIGraph(graph, nx.DiGraph(), {},
                            bt_factory=bt_factory)
    type_eraser = te.TypeEraser(api_graph, bt_factory, False)

    func_call = mk_method_call("foo", [kt.Boolean], [kt.Boolean],
                               kt.Boolean, kt.Boolean)

    cond = ast.Conditional(func_call, ast.BottomConstant(kt.String),
                           ast.BottomConstant(kt.String),
                           inferred_type=kt.String)

    assert not func_call.can_infer_type_args
    type_eraser.erase_types_ill_typed(func_call, m, kt.Number, 0,
                                      [(cond, 0)])
    assert not func_call.can_infer_type_args

    # Case 2:
    # <T> T m(T x)
    # String x = if (true) m<String>("") else "" -> if (true) m(1) else ""
    func_call = mk_method_call("foo", [kt.String], [kt.String],
                               kt.String, kt.String)
    cond = ast.Conditional(ast.BooleanConstant("true"),
                           func_call,
                           ast.BottomConstant(kt.String),
                           inferred_type=kt.String)
    var_decl = mk_var_decl(kt.String)
    assert not func_call.can_infer_type_args
    type_eraser.erase_types_ill_typed(func_call, m, kt.Number, 0,
                                      [(cond, 1), (var_decl, 0)])
    assert func_call.can_infer_type_args or var_decl.var_type is None


def test_erase_types_ill_typed_params():

    bt_factory = kt.KotlinBuiltinFactory()
    type_param = tp.TypeParameter("T")

    # Case 1:
    # <T> T m(T x)
    # void g(int x)
    # g(m<Integer>(1)) -> g(m("")): Can erase types
    graph = nx.DiGraph()
    m = mk_method(graph, "foo", [type_param],
                  type_param, [type_param])
    mk_method(graph, "g", [kt.Integer], kt.Integer, [])
    api_graph = ag.APIGraph(graph, nx.DiGraph(), {},
                            bt_factory=bt_factory)
    type_eraser = te.TypeEraser(api_graph, bt_factory, False)

    func_call = mk_method_call("foo", [kt.Integer], [kt.Integer],
                               kt.Integer, kt.Integer)
    g_func_call = mk_method_call("g", [kt.Integer], [], kt.Integer,
                                 None)

    assert not func_call.can_infer_type_args
    type_eraser.erase_types_ill_typed(func_call, m, kt.String, 0,
                                      [(g_func_call, 0)])
    assert func_call.can_infer_type_args

    # Case 2:
    # <T> T m(T x)
    # <T> Int g(T x)
    # g(m<Integer>(1)) -> g<Integer>(m(""))
    graph = nx.DiGraph()
    m = mk_method(graph, "foo", [type_param],
                  type_param, [type_param])
    mk_method(graph, "g", [type_param], kt.Integer, [type_param])
    api_graph = ag.APIGraph(graph, nx.DiGraph(), {},
                            bt_factory=bt_factory)
    type_eraser = te.TypeEraser(api_graph, bt_factory, False)

    func_call = mk_method_call("foo", [kt.Integer], [kt.Integer],
                               kt.Integer, kt.Integer)
    g_func_call = mk_method_call("g", [kt.Integer], [kt.Integer], kt.Integer,
                                 None)

    assert not func_call.can_infer_type_args
    type_eraser.erase_types_ill_typed(func_call, m, kt.String, 0,
                                      [(g_func_call, 0)])
    assert func_call.can_infer_type_args and not g_func_call.can_infer_type_args

    # Case 3
    # <T> T m(T x)
    # <T> Int g(T x)
    # Int x = g(m<Integer>(1)) -> Int x = g<Integer>(m("")):
    func_call = mk_method_call("foo", [kt.Integer], [kt.Integer],
                               kt.Integer, kt.Integer)
    g_func_call = mk_method_call("g", [kt.Integer], [kt.Integer], kt.Integer,
                                 kt.Integer)

    var_decl = mk_var_decl(kt.Integer)
    assert not func_call.can_infer_type_args
    type_eraser.erase_types_ill_typed(func_call, m, kt.String, 0,
                                      [(g_func_call, 0), (var_decl, 0)])
    assert func_call.can_infer_type_args and not g_func_call.can_infer_type_args

    # Case 4
    # <T> T m(T x)
    # <T> T g(T x)
    # Integer x = g(m<Integer>(1)) -> Integer x = g(m(""))
    graph = nx.DiGraph()
    m = mk_method(graph, "foo", [type_param],
                  type_param, [type_param])
    mk_method(graph, "g", [type_param], type_param, [type_param])
    api_graph = ag.APIGraph(graph, nx.DiGraph(), {},
                            bt_factory=bt_factory)
    type_eraser = te.TypeEraser(api_graph, bt_factory, False)

    func_call = mk_method_call("foo", [kt.Integer], [kt.Integer],
                               kt.Integer, kt.Integer)
    g_func_call = mk_method_call("g", [kt.Integer], [kt.Integer], kt.Integer,
                                 kt.Integer)
    var_decl = mk_var_decl(kt.Integer)

    assert not func_call.can_infer_type_args
    type_eraser.erase_types_ill_typed(func_call, m, kt.String, 0,
                                      [(g_func_call, 0), (var_decl, 0)])

    assert func_call.can_infer_type_args and \
        (g_func_call.can_infer_type_args or var_decl.var_type is None)

    # Case 5
    # <T> T m(T x)
    # <T> T g(int x, T y)
    # g<String>(m<Integer>(1), "") -> g<String>(m(""), "")
    graph = nx.DiGraph()
    m = mk_method(graph, "foo", [type_param],
                  type_param, [type_param])
    mk_method(graph, "g", [kt.Integer, type_param], type_param, [type_param])
    api_graph = ag.APIGraph(graph, nx.DiGraph(), {},
                            bt_factory=bt_factory)
    type_eraser = te.TypeEraser(api_graph, bt_factory, False)

    func_call = mk_method_call("foo", [kt.Integer], [kt.Integer],
                               kt.Integer, kt.Integer)
    g_func_call = mk_method_call("g", [kt.Integer, kt.String],
                                 [kt.String], kt.String, None)

    assert not func_call.can_infer_type_args
    type_eraser.erase_types_ill_typed(func_call, m, kt.String, 0,
                                      [(g_func_call, 0)])

    assert func_call.can_infer_type_args and g_func_call.can_infer_type_args

    # Case 6
    # <T> T m(T x)
    # <T> T g(T x)
    # Integer x = if (true) g(m<Integer>(1)) else 2 ->
    # Integer x = if (true) g(m("")) else 2
    graph = nx.DiGraph()
    m = mk_method(graph, "foo", [type_param],
                  type_param, [type_param])
    mk_method(graph, "g", [type_param], type_param, [type_param])
    api_graph = ag.APIGraph(graph, nx.DiGraph(), {},
                            bt_factory=bt_factory)
    type_eraser = te.TypeEraser(api_graph, bt_factory, False)

    func_call = mk_method_call("foo", [kt.Integer], [kt.Integer],
                               kt.Integer, kt.Integer)
    g_func_call = mk_method_call("g", [kt.Integer], [kt.Integer], kt.Integer,
                                 kt.Integer)
    cond = ast.Conditional(ast.BooleanConstant("true"), g_func_call,
                           ast.BottomConstant(kt.Integer),
                           inferred_type=kt.Integer)
    var_decl = mk_var_decl(kt.Integer)

    assert not func_call.can_infer_type_args
    type_eraser.erase_types_ill_typed(func_call, m, kt.String, 0,
                                      [(g_func_call, 0), (cond, 1),
                                       (var_decl, 0)])
    assert func_call.can_infer_type_args and \
        (g_func_call.can_infer_type_args or var_decl.var_type is None)


def test_erase_types_illtyped_mix_receiver_polymorphic():
    bt_factory = kt.KotlinBuiltinFactory()
    type_param1 = tp.TypeParameter("T1")
    type_param2 = tp.TypeParameter("T2")

    type_con = tp.TypeConstructor("A", [type_param1])

    # Case 1:
    # class A<T> { <Y> void m(T x) }
    # a.m<String>("") -> a.m(1): Invalid, erase types

    graph = nx.DiGraph()
    m = mk_method(graph, "foo", [type_param1], bt_factory.get_void_type(),
                  [type_param2])
    api_graph = ag.APIGraph(graph, nx.DiGraph(), {},
                            bt_factory=bt_factory)
    type_eraser = te.TypeEraser(api_graph, bt_factory, False)

    func_call = mk_method_call("foo", [kt.String], [kt.String],
                               bt_factory.get_void_type())
    func_call.receiver = mk_expr(type_con.new([kt.String]))

    assert not func_call.can_infer_type_args
    type_eraser.erase_types_ill_typed(func_call, m, kt.Number, 0, [])
    assert func_call.can_infer_type_args


def test_erase_types_illtyped_receiver():
    bt_factory = kt.KotlinBuiltinFactory()
    # Case 1:
    # <T> A<T> m1()
    # class A<T> { m2(T x) }
    # m1<String>().m2("") -> m1().m2(""): Erase types OK

    type_param1 = tp.TypeParameter("T1")
    type_param2 = tp.TypeParameter("T2")
    type_con = tp.TypeConstructor("A", [type_param1])

    graph = nx.DiGraph()
    m1 = mk_method(graph, "m1", [type_param1], type_con.new([type_param1]),
                   [type_param1])
    mk_method(graph, "m2", [type_param2], bt_factory.get_void_type(),
              [], type_con)
    api_graph = ag.APIGraph(graph, nx.DiGraph(), {},
                            bt_factory=bt_factory)
    type_eraser = te.TypeEraser(api_graph, bt_factory, False)

    func_call = mk_method_call("m1", [], [kt.String],
                               type_con.new([kt.String]), expected_type=None)
    func_call2 = mk_method_call("m2", [kt.String], [],
                                bt_factory.get_void_type())
    func_call2.receiver = func_call

    assert not func_call.can_infer_type_args
    type_eraser.erase_types_ill_typed(func_call, m1, kt.Number, 0,
                                      [(func_call2, -1)])
    assert func_call.can_infer_type_args

    # Case 2:
    # <T> A<T> m1()
    # class A<T> { T m2() }
    # String x = m1<String>().m2() -> String m1(1).m2(): Erase types OK
    graph = nx.DiGraph()
    m1 = mk_method(graph, "m1", [type_param1], type_con.new([type_param1]),
                   [type_param1])
    mk_method(graph, "m2", [], type_param2, [], type_con)
    api_graph = ag.APIGraph(graph, nx.DiGraph(), {},
                            bt_factory=bt_factory)
    type_eraser = te.TypeEraser(api_graph, bt_factory, False)

    func_call = mk_method_call("m1", [], [kt.String],
                               type_con.new([kt.String]), expected_type=None)
    func_call2 = mk_method_call("m2", [], [], kt.String, kt.String)
    func_call2.receiver = func_call
    var_decl = mk_var_decl(kt.String)

    assert not func_call.can_infer_type_args
    type_eraser.erase_types_ill_typed(func_call, m1, kt.Number, 0,
                                      [(func_call2, -1)])
    assert not func_call.can_infer_type_args

    assert not func_call.can_infer_type_args
    type_eraser.erase_types_ill_typed(func_call, m1, kt.Number, 0,
                                      [(func_call2, -1), (var_decl, 0)])
    assert func_call.can_infer_type_args

    # Case 3
    # <T> A<T> m1(T x)
    # class A<T> { B<T> get() }
    # class B<T> { m3(T x) }
    #
    type_param3 = tp.TypeParameter("T3")
    a = tp.TypeConstructor("A", [type_param2])
    b = tp.TypeConstructor("B", [type_param3])
    graph = nx.DiGraph()
    m1 = mk_method(graph, "m1", [type_param1], a.new([type_param1]),
                   [type_param1])
    mk_method(graph, "m2", [], b.new([type_param2]), [], a)
    mk_method(graph, "m3", [type_param3], bt_factory.get_void_type(), [], b)
    api_graph = ag.APIGraph(graph, nx.DiGraph(), {},
                            bt_factory=bt_factory)
    type_eraser = te.TypeEraser(api_graph, bt_factory, False)
    m1_call = mk_method_call("m1", [kt.String], [kt.String],
                             a.new([kt.String]))
    m2_call = mk_method_call("m2", [], [], b.new([kt.String]))
    m2_call.receiver = m1_call
    m3_call = mk_method_call("m3", [kt.String], [], bt_factory.get_void_type())
    m3_call.receiver = m2_call

    assert not m1_call.can_infer_type_args
    type_eraser.erase_types_ill_typed(m1_call, m1, kt.Number, 0,
                                      [(m2_call, -1), (m3_call, -1)])
    assert m1_call.can_infer_type_args

    # Case 4
    # <T> A<T> m1(T x)
    # class A<T> { B<T> get() }
    # class B<T> { m3(int x) }
    graph = nx.DiGraph()
    m1 = mk_method(graph, "m1", [type_param1], a.new([type_param1]),
                   [type_param1])
    mk_method(graph, "m2", [], b.new([type_param2]), [], a)
    mk_method(graph, "m3", [kt.Integer], bt_factory.get_void_type(), [], b)
    api_graph = ag.APIGraph(graph, nx.DiGraph(), {},
                            bt_factory=bt_factory)
    type_eraser = te.TypeEraser(api_graph, bt_factory, False)
    m1_call = mk_method_call("m1", [kt.String], [kt.String],
                             a.new([kt.String]))
    m2_call = mk_method_call("m2", [], [], b.new([kt.String]))
    m2_call.receiver = m1_call
    m3_call = mk_method_call("m3", [kt.Integer], [],
                             bt_factory.get_void_type())
    m3_call.receiver = m2_call

    assert not m1_call.can_infer_type_args
    type_eraser.erase_types_ill_typed(m1_call, m1, kt.Number, 0,
                                      [(m2_call, -1), (m3_call, -1)])
    assert not m1_call.can_infer_type_args

    # Case 5
    # <T> T m1(T x)
    # class A<T> { B<T> get() }
    graph = nx.DiGraph()
    m1 = mk_method(graph, "m1", [type_param1], type_param1,
                   [type_param1])
    mk_method(graph, "m2", [], b.new([type_param2]), [], a)
    api_graph = ag.APIGraph(graph, nx.DiGraph(), {},
                            bt_factory=bt_factory)
    type_eraser = te.TypeEraser(api_graph, bt_factory, False)
    t = a.new([kt.String])
    m1_call = mk_method_call("m1", [t], [t], t)
    m2_call = mk_method_call("m2", [], [], b.new([kt.String]))
    m2_call.receiver = m1_call

    assert not m1_call.can_infer_type_args
    type_eraser.erase_types_ill_typed(m1_call, m1, kt.Number, 0,
                                      [(m2_call, -1)])
    assert m1_call.can_infer_type_args

    m1_call.recover_types()
    assert not m1_call.can_infer_type_args
    type_eraser.erase_types_ill_typed(m1_call, m1, a.new([kt.Any]), 0,
                                      [(m2_call, -1)])
    assert not m1_call.can_infer_type_args


def test_erase_types_illtyped_receiver_field():
    # A<T> m1(T x)
    # class A<T> { T f }
    bt_factory = kt.KotlinBuiltinFactory()
    type_param1 = tp.TypeParameter("T1")
    type_param2 = tp.TypeParameter("T2")
    a = tp.TypeConstructor("A", [type_param2])

    graph = nx.DiGraph()
    m1 = mk_method(graph, "m1", [type_param1], a.new([type_param1]),
                   [type_param1])
    mk_field(graph, "f", type_param2, a)
    api_graph = ag.APIGraph(graph, nx.DiGraph(), {},
                            bt_factory=bt_factory)
    type_eraser = te.TypeEraser(api_graph, bt_factory, False)

    m1_call = mk_method_call("m1", [kt.String], [kt.String],
                             a.new([kt.String]))
    field_acc = ast.FieldAccess(m1_call, "f")
    field_acc.mk_typed(ast.TypePair(expected=kt.String, actual=kt.String))

    assert not m1_call.can_infer_type_args
    type_eraser.erase_types_ill_typed(m1_call, m1, kt.Number, 0,
                                      [(field_acc, 0)])
    assert not m1_call.can_infer_type_args

    var_decl = mk_var_decl(kt.String)
    m1_call.recover_types()
    assert not m1_call.can_infer_type_args
    type_eraser.erase_types_ill_typed(m1_call, m1, kt.Number, 0,
                                      [(field_acc, 0), (var_decl, 0)])
    assert m1_call.can_infer_type_args or var_decl.var_type is None

    # A<T> m1(T x)
    # class A<T> { B<T> f }
    # class B<T> { T f }
    # String x = m1<String>("f").f.f
    type_param3 = tp.TypeParameter("T3")
    b = tp.TypeConstructor("B", [type_param3])

    graph = nx.DiGraph()
    m1 = mk_method(graph, "m1", [type_param1], a.new([type_param1]),
                   [type_param1])
    mk_field(graph, "f", b.new([type_param2]), a)
    mk_field(graph, "f", type_param3, b)
    api_graph = ag.APIGraph(graph, nx.DiGraph(), {},
                            bt_factory=bt_factory)
    type_eraser = te.TypeEraser(api_graph, bt_factory, False)

    m1_call = mk_method_call("m1", [kt.String], [kt.String],
                             a.new([kt.String]))
    t1 = a.new([kt.String])
    field_acc1 = ast.FieldAccess(m1_call, "f")
    field_acc1.mk_typed(ast.TypePair(expected=t1, actual=t1))
    field_acc2 = ast.FieldAccess(field_acc1, "f")
    field_acc2.mk_typed(ast.TypePair(expected=kt.String, actual=kt.String))

    assert not m1_call.can_infer_type_args
    type_eraser.erase_types_ill_typed(m1_call, m1, kt.Number, 0,
                                      [(field_acc1, 0), (field_acc2, 0)])
    assert not m1_call.can_infer_type_args

    var_decl = mk_var_decl(kt.String)
    m1_call.recover_types()
    assert not m1_call.can_infer_type_args
    type_eraser.erase_types_ill_typed(m1_call, m1, kt.Number, 0,
                                      [(field_acc1, 0), (field_acc2, 0),
                                       (var_decl, 0)])
    assert m1_call.can_infer_type_args
