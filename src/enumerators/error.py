from abc import ABC, abstractmethod
from copy import deepcopy

from src.ir import ast
from src.ir.builtins import BuiltinFactory
from src.ir.visitors import DefaultVisitor, ASTExprUpdate
from src.generators import Generator


class ErrorEnumerator(ABC, DefaultVisitor):
    def __init__(self, program: ast.Program, program_gen: Generator,
                 bt_factory: BuiltinFactory):
        self.program = deepcopy(program)
        self.program_gen = program_gen
        self.bt_factory = bt_factory
        self.error_injected: str = None
        self.programs_enum = self.enumerate_programs()

    @abstractmethod
    def get_candidate_program_locations(self):
        pass

    @abstractmethod
    def filter_program_locations(self, locations):
        pass

    @abstractmethod
    def get_programs_with_error(self, location):
        pass

    @abstractmethod
    def add_err_message(self, loc, new_node, *args):
        pass

    def enumerate_programs(self):
        locations = self.get_candidate_program_locations()
        locations = self.filter_program_locations(locations)
        prev_loc = None
        for loc in locations:
            if prev_loc:
                upd = ASTExprUpdate(prev_loc.index, prev_loc.expr)
                upd.visit(prev_loc.parent)
            prev_loc = loc
            yield from self.get_programs_with_error(loc)
