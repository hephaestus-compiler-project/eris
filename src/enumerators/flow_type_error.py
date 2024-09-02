from copy import deepcopy

from src.ir import ast
from src.ir.visitors import ASTExprUpdate
from src.generators.api import nodes
from src.ir.builtins import BuiltinFactory
from src.generators import Generator
from src.enumerators.analyses import BlockAnalysis, Loc
from src.enumerators.error import ErrorEnumerator


class FlowBasedTypeErrorEnumerator(ErrorEnumerator):
    name = "FlowBasedTypeErrorEnumerator"

    def __init__(self, program: ast.Program, program_gen: Generator,
                 bt_factory: BuiltinFactory):
        self.api_graph = program_gen.api_graph
        self.error_loc = None
        self.new_node = None
        self.analysis = BlockAnalysis()
        super().__init__(program, program_gen, bt_factory)

    def get_candidate_program_locations(self):
        self.analysis.visit_program(self.program)
        self.flow_variable = self.analysis.flow_variables[0]
        print(self.flow_variable)
        return self.analysis.locations

    def filter_program_locations(self, locations):
        return locations

    def get_programs_with_error(self, location):
        # var_type = self.api_graph.get_concrete_output_type(
        #    nodes.Variable(self.flow_variable))
        assignment = ast.Assignment(
            self.flow_variable,
            self.program_gen.generate_expr(self.bt_factory.get_integer_type())
        )
        new_expr = deepcopy(location.expr)
        new_expr.body.append(assignment)
        upd = ASTExprUpdate(location.index, new_expr)
        upd.visit(location.parent)
        self.add_err_message(location, assignment)
        yield self.program

    def add_err_message(self, loc, new_node, *args):
        self.error_loc = loc
        self.new_node = new_node

    @property
    def error_explanation(self):
        if self.error_loc is None:
            return

        var_type = self.api_graph.get_concrete_output_type(
            nodes.Variable(self.flow_variable))
        # Get the string representation of expressions
        translator = self.program_gen.translator
        translator.context = self.program.context
        translator.visit(self.new_node.expr)
        expr_str = translator._children_res[-1]
        translator._reset_state()
        msg = (f"Added assignment for variable {self.flow_variable}\n"
               f" - Expected type {var_type}\n"
               f" - New expression {expr_str}\n"
               f" - Parent block: {type(self.error_loc.parent)}")
        return msg
