from src.ir import ast, types as tp, kotlin_types as kt
from src.enumerators import FlowBasedTypeErrorEnumerator


def to_expr(t: tp.Type) -> ast.Expr:
    expr = ast.BottomConstant(t)
    expr.mk_typed(ast.TypePair(t, t))
    return expr


def test_block_analysis():
    var_decl = ast.VariableDeclaration("x", to_expr(kt.String),
                                       is_final=False, var_type=kt.String)
    var_decl.var_type = kt.Any
    true_block = ast.Block([ast.Assignment("x", to_expr(kt.String))])
    false_block = ast.Block([])
    cond = ast.Conditional(to_expr(kt.Boolean), true_block, false_block,
                           kt.Unit, is_expression=False)
    loop_block = ast.Block([
        ast.Assignment("x", to_expr(kt.String)),
        cond,
        ast.VariableDeclaration("y", ast.Variable("x"), is_final=True,
                                var_type=kt.String)
    ])
    loop = ast.Loop(loop_block)
    func_decl = ast.FunctionDeclaration(
        "test",
        [],
        kt.Unit,
        ast.Block([loop]),
        ast.FunctionDeclaration.FUNCTION
    )

    enumerator = FlowBasedTypeErrorEnumerator(func_decl, None,
                                              kt.KotlinBuiltinFactory())
    enumerator.flow_variable = "x"
    enumerator.target_node = 4
    locations = enumerator.get_candidate_program_locations()
    assert len(locations) == 6
    new_locations = enumerator.filter_program_locations(locations)
    inverse_map = {v: k for k, v in enumerator.location_map.items()}
    assert [inverse_map[loc] for loc in new_locations] == [1, 2, 3]
