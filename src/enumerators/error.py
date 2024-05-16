from abc import ABC, abstractmethod
from copy import deepcopy

from src.ir import ast
from src.ir.builtins import BuiltinFactory
from src.ir.visitors import DefaultVisitor
from src.generators import Generator
from src.enumerators.updater import ProgramUpdate


class ErrorEnumerator(ABC, DefaultVisitor):
    def __init__(self, program: ast.Program, program_gen: Generator,
                 bt_factory: BuiltinFactory):
        self.initial_program = deepcopy(program)
        self.program = program
        self.program_gen = program_gen
        self.bt_factory = bt_factory
        self.error_injected: bool = None
        self._has_next: bool = True
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
    def add_err_message(self, loc, new_node):
        pass

    def enumerate_programs(self):
        locations = self.get_candidate_program_locations()
        locations = self.filter_program_locations(locations)
        for loc in locations:
            yield from self.get_programs_with_error(loc)
            upd = ProgramUpdate(loc.index, loc.expr)
            upd.visit(loc.parent)

    def has_next(self) -> bool:
        return self._has_next

    def reset_state(self):
        self.program = deepcopy(self.initial_program)

    def gen_next_program(self) -> ast.Program:
        program = next(self.programs_enum, None)
        if not program:
            self._has_next = False
            program = None

        return program
