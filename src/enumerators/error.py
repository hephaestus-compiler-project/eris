from abc import ABC, abstractmethod

from src.ir import ast
from src.ir.builtins import BuiltinFactory
from src.ir.visitors import DefaultVisitor
from src.generators.api import api_graph as ag


class ErrorEnumerator(ABC, DefaultVisitor):
    def __init__(self, program: ast.Program, api_graph: ag.APIGraph,
                 bt_factory: BuiltinFactory):
        self.program = program
        self.api_graph = api_graph
        self.bt_factory = bt_factory
        self.error_injected: bool = False
        self._has_next: bool = True

    @abstractmethod
    def get_candidate_program_locations(self):
        pass

    @abstractmethod
    def filter_program_locations(self, locations):
        pass

    @abstractmethod
    def enumerate_programs(locations):
        pass

    def has_next(self) -> bool:
        return self._has_next

    def gen_next_program(self) -> ast.Program:
        locations = self.get_candidate_program_locations()
        locations = self.filter_program_locations(locations)
        programs_gen = self.enumerate_programs(locations)
        return 0
        # program = next(programs_gen, None)
        # if not program:
        #     self._has_next = False
        #     program = None

        # return program
