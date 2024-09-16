from copy import deepcopy
from typing import NamedTuple, List, Dict, Tuple

import networkx as nx

from src.ir import ast, types as tp
from src.ir.visitors import DefaultVisitor


class Loc(NamedTuple):
    expr: ast.Expr
    parent: ast.Node
    index: int
    depth: int
    scope: dict

    RECEIVER_INDEX = -1

    def is_receiver_loc(self):
        if isinstance(self.parent, (ast.FieldAccess, ast.FunctionReference)):
            return True
        if (isinstance(self.parent, ast.FunctionCall)
                and self.parent.receiver is not None
                and self.index == self.RECEIVER_INDEX):
            return True

        if (isinstance(self.parent, ast.Assignment)
                and self.parent.receiver is not None
                and self.index == self.RECEIVER_INDEX):
            return True
        return False

    def is_parent_call(self):
        return isinstance(self.parent, (ast.FunctionCall, ast.New))

    def is_parent_var_decl(self):
        return isinstance(self.parent, ast.VariableDeclaration)

    def get_parent_expected_type(self, api_graph):
        if self.parent is None:
            return None
        if isinstance(self.parent, ast.VariableDeclaration):
            return self.parent.var_type
        elif isinstance(self.parent, ast.FunctionDeclaration):
            return self.parent.ret_type
        elif isinstance(self.parent, ast.Assignment):
            if self.parent.receiver is not None:
                rec_t = self.parent.receiver.get_type_info()[1]
                sub = {}
                if rec_t.is_parameterized():
                    sub = rec_t.get_type_variable_assignments()
                decl = api_graph.get_declaration_of_access(
                    self.parent, only_instance=True)
                out_type = api_graph.get_concrete_output_type(decl)
                out_type = tp.substitute_type(out_type, sub)
                return out_type
            else:
                return None
        elif isinstance(self.parent, ast.FunctionCall):
            if self.index == self.RECEIVER_INDEX:
                return None
            decl = api_graph.get_declaration_of_access(self.parent,
                                                       only_instance=False)
            if decl is None:
                # FIXME
                return None
            try:
                param_t = decl.parameters[self.index].t
                sub = {}
                if self.parent.receiver is not None:
                    rec_t = self.parent.receiver.get_type_info()[1]
                    if rec_t.is_parameterized():
                        sub = rec_t.get_type_variable_assignments()
                param_t = tp.substitute_type(param_t, sub)
                return param_t
            except IndexError:
                return None
        else:
            return None


class LocationAnalysis(DefaultVisitor):
    def __init__(self):
        self.locations: List[Loc] = []


class BlockAnalysis(LocationAnalysis):
    """
    An analysis used to identify all the available blocks in the program.
    Also, it constructs the CFG of every function in the program.
    """
    def __init__(self):
        super().__init__()

        # Map every function with its cfg
        self.cfgs = {}
        # Keep the name of the current function
        self.func_name: str = None
        # A stack with the blocks
        self.blocks = []
        # Map node ids with the corresponding blocks
        self.block_map = {}
        # Generator for node ids
        self.id_gen = iter(range(10000))
        # Track the parents of blocks in the AST
        self.block_parents = {}
        self.green_blocks: Dict[ast.VariableDeclaration, List[ast.Block]] = {}
        self.variables: Dict[str, ast.VariableDeclaration] = {}

    def get_parent_of_block(self, block_id: int):
        block = self.block_map.get(block_id)
        if block is None:
            return None
        return self.block_parents[block]

    def pop_parent_block(self):
        if self.blocks:
            return self.blocks.pop()
        return None

    def get_parent_block(self):
        if self.blocks:
            return self.blocks[-1]
        return None

    def add_block_to_stack(self, block_id):
        node = self.blocks.append(block_id)
        return node

    def get_current_cfg(self):
        if self.func_name:
            return self.cfgs[self.func_name]
        return None

    def add_block_to_cfg(self, cfg, block):
        node_id = next(self.id_gen)
        cfg.add_node(node_id)
        self.block_map[node_id] = block
        return node_id

    def add_cfg(self, func_name: str, cfg: nx.DiGraph):
        self.cfgs[func_name] = cfg

    def add_merge_node(self):
        parent_id = self.get_parent_block()
        parent_block = self.block_map[parent_id]
        cfg = self.get_current_cfg()
        leaf_nodes = [n for n in cfg.nodes()
                      if cfg.out_degree(n) == 0 and nx.has_path(
                          cfg, parent_id, n)]
        if len(leaf_nodes) > 1:
            merge_node = self.add_block_to_cfg(cfg, parent_block)
            for leaf in leaf_nodes:
                cfg.add_edge(leaf, merge_node)
            self.pop_parent_block()
            self.add_block_to_stack(merge_node)

    def visit_var_decl(self, node):
        self.variables[node.name] = node

    def visit_assign(self, node):
        parent_id = self.get_parent_block()
        if isinstance(node.expr, ast.Variable):
            # Revisit
            cfg = self.get_current_cfg()
            source = 0  # This is the root node
            target = parent_id
            var_decl = self.variables[node.expr.name]
            if source == target:
                is_green = target in self.green_blocks.get(var_decl, set())
            else:
                is_green = True
                for path in nx.all_simple_paths(cfg, source, target):
                    pp = path
                    if all(p not in self.green_blocks.get(var_decl, set())
                           for p in pp):
                        is_green = False
                        break
                if is_green:
                    self.green_blocks.setdefault(
                        self.variables[node.name], set().add(target))
        else:
            self.green_blocks.setdefault(
                self.variables[node.name], set()).add(parent_id)

    def visit_func_decl(self, node):
        prev_func_name = self.func_name
        self.func_name = node.name
        is_block = False
        if node.body and isinstance(node.body, ast.Block):
            cfg = nx.DiGraph()
            self.add_cfg(node.name, cfg)
            block_id = self.add_block_to_cfg(cfg, node.body)
            self.add_block_to_stack(block_id)
            self.block_parents[node.body] = (node, 0)
            is_block = True
        super().visit_func_decl(node)
        if is_block:
            self.pop_parent_block()
        self.func_name = prev_func_name

    def visit_conditional(self, node):
        parent_id = self.get_parent_block()
        cfg = self.get_current_cfg()
        self.visit(node.cond)  # Visit conditional
        branches = [node.true_branch, node.false_branch]
        for branch in branches:
            is_block = isinstance(branch, ast.Block)
            child_id = None
            if is_block:
                child_id = self.add_block_to_cfg(cfg, branch)
                cfg.add_edge(parent_id, child_id)
                self.add_block_to_stack(child_id)
            self.visit(branch)
            if is_block:
                self.pop_parent_block()

        if node.true_branch and isinstance(node.true_branch, ast.Block):
            self.block_parents[node.true_branch] = (node, 1)
        if node.false_branch and isinstance(node.false_branch, ast.Block):
            self.block_parents[node.false_branch] = (node, 2)
        self.add_merge_node()

    def visit_multiconditional(self, node):
        parent_id = self.get_parent_block()
        cfg = self.get_current_cfg()
        self.visit(node.root_cond)
        for cond in node.conditions:
            self.visit(cond)
        for branch in node.branches:
            is_block = isinstance(branch, ast.Block)
            child_id = None
            if is_block:
                child_id = self.add_block_to_cfg(cfg, branch)
                cfg.add_edge(parent_id, child_id)
                self.add_block_to_stack(child_id)
            self.visit(branch)
            if is_block:
                self.pop_parent_block()
        i = 0
        if node.root_cond is not None:
            i += 1
        i = i + len(node.conditions)
        for j, branch in enumerate(node.branches):
            if isinstance(branch, ast.Block):
                self.block_parents[branch] = (node, i + j)
        self.add_merge_node()

    def visit_trycatch(self, node):
        parent_id = self.get_parent_block()
        cfg = self.get_current_cfg()
        branches = [node.try_block]
        for branch in branches:
            is_block = isinstance(branch, ast.Block)
            child_id = None
            if is_block:
                child_id = self.add_block_to_cfg(cfg, branch)
                cfg.add_edge(parent_id, child_id)
                self.add_block_to_stack(child_id)
            self.visit(branch)
            if is_block:
                self.pop_parent_block()
        branches = list(node.catch_blocks.values())
        exc_id = next(self.id_gen)
        cfg.add_node(exc_id)
        cfg.add_edge(parent_id, exc_id)
        for branch in branches:
            is_block = isinstance(branch, ast.Block)
            child_id = None
            if is_block:
                child_id = self.add_block_to_cfg(cfg, branch)
                cfg.add_edge(exc_id, child_id)
                self.add_block_to_stack(child_id)
            self.visit(branch)
            if is_block:
                self.pop_parent_block()
        if isinstance(node.try_block, ast.Block):
            self.block_parents[node.try_block] = (node, 0)
        for i, block in enumerate(node.catch_blocks.values()):
            self.block_parents[block] = (node, i + 1)
        self.add_merge_node()

    def visit_loop(self, node):
        parent_id = self.get_parent_block()
        cfg = self.get_current_cfg()
        branches = [node.block]
        for branch in branches:
            is_block = isinstance(branch, ast.Block)
            child_id = None
            if is_block:
                child_id = self.add_block_to_cfg(cfg, branch)
                cfg.add_edge(parent_id, child_id)
                self.add_block_to_stack(child_id)
            self.visit(branch)
            if is_block:
                self.pop_parent_block()
        if isinstance(node.block, ast.Block):
            self.block_parents[node.block] = (node, 0)

        parent_block = self.block_map[parent_id]
        # Create a node that represents the exit of the loop
        merge_node = self.add_block_to_cfg(cfg, parent_block)

        # Connect the parent node with the exit node.
        cfg.add_edge(parent_id, merge_node)

        # Try to find all leaf nodes created inside the loop.
        leaf_nodes = [n for n in cfg.nodes()
                      if cfg.out_degree(n) == 0 and nx.has_path(
                          cfg, child_id, n)]
        if not leaf_nodes:
            leaf_nodes = [child_id]
        assert len(leaf_nodes) == 1
        # Connect these leaf nodes with the exit node.
        cfg.add_edge(leaf_nodes[0], merge_node)
        # Connect these leaf nodes with the entry of the loop.
        cfg.add_edge(leaf_nodes[0], child_id, cycle=True)


class ExprLocationAnalysis(LocationAnalysis):
    def __init__(self):
        super().__init__()
        self.depth = 0
        self.namespace = tuple()
        self.scope = {
            "local_vars": {},
            "local_types": {},
        }
        self.parents: Dict[ast.Node, Tuple[ast.Node, int]] = {}

    def push_local_var(self, var_name: str, var_type):
        self.scope["local_vars"][var_name] = var_type

    def push_local_type(self, t: tp.Type):
        self.scope["local_types"][t.name] = t

    def pop_local_var(self, var_name: str):
        del self.scope["local_vars"][var_name]

    def pop_local_type(self, type_name: str):
        del self.scope["local_types"][type_name]

    def visit_class_decl(self, node):
        prev_namespace = self.namespace
        self.namespace += (node.name,)
        for type_param in node.type_parameters:
            self.push_local_type(type_param)
        super().visit_class_decl(node)
        for type_param in node.type_parameters:
            self.pop_local_type(type_param.name)
        self.namespace = prev_namespace

    def visit_block(self, node):
        super().visit_block(node)
        for i, elem in enumerate(node.body):
            if isinstance(elem, ast.Expr):
                self.parents[elem] = (node, i)
                self.locations.append(Loc(elem, node, i, self.depth,
                                          deepcopy(self.scope)))

    def visit_var_decl(self, node):
        prev_depth = self.depth
        self.depth += 1
        super().visit_var_decl(node)
        self.depth = prev_depth
        self.parents[node.expr] = (node, 0)
        self.locations.append(Loc(node.expr, node, 0, self.depth,
                                  deepcopy(self.scope)))

    def visit_func_decl(self, node):
        if not node.metadata.get("is_static"):
            self.push_local_var("this", self.namespace[-1])
        for p in node.params:
            self.push_local_var(p.name, p.get_type())
        for type_param in node.type_parameters:
            self.push_local_type(type_param)

        prev_depth = self.depth
        self.depth += 1
        super().visit_func_decl(node)
        self.depth = prev_depth
        if node.body and isinstance(node.body, ast.Expr):
            self.parents[node.body] = (node, 0)
            self.locations.append(Loc(node.body, node, 0, self.depth,
                                      deepcopy(self.scope)))
        if not node.metadata.get("is_static"):
            self.pop_local_var("this")
        for p in node.params:
            self.pop_local_var(p.name)
        for type_param in node.type_parameters:
            self.pop_local_type(type_param.name)

    def visit_lambda(self, node):
        for p in node.params:
            self.push_local_var(p.name, p.get_type())
        prev_depth = self.depth
        self.depth += 1
        super().visit_lambda(node)
        self.depth = prev_depth
        if isinstance(node.body, ast.Expr):
            self.parents[node.body] = (node, 0)
            self.locations.append(Loc(node.body, node, 0, self.depth,
                                      deepcopy(self.scope)))
        for p in node.params:
            self.pop_local_var(p.name)

    def visit_func_ref(self, node):
        prev_depth = self.depth
        self.depth += 1
        super().visit_func_ref(node)
        self.depth = prev_depth
        if node.receiver:
            self.parents[node.receiver] = (node, 0)
            self.locations.append(Loc(node.receiver, node, 0, self.depth,
                                      deepcopy(self.scope)))

    def visit_array_expr(self, node):
        prev_depth = self.depth
        self.depth += 1
        super().visit_array_expr(node)
        self.depth = prev_depth
        for i, expr in enumerate(node.exprs):
            self.parents[expr] = (node, i)
            self.locations.append(Loc(expr, node, i, self.depth,
                                      deepcopy(self.scope)))

    def visit_unary_expr(self, node):
        prev_depth = self.depth
        self.depth += 1
        super().visit_unary_expr(node)
        self.depth = prev_depth
        self.parents[node.expr] = (node, 0)
        self.locations.append(Loc(node.expr, node, 0, self.depth,
                                  deepcopy(self.scope)))

    def visit_binary_expr(self, node):
        prev_depth = self.depth
        self.depth += 1
        super().visit_binary_expr(node)
        self.depth = prev_depth
        self.parents[node.lexpr] = (node, 0)
        self.locations.append(Loc(node.lexpr, node, 0, self.depth,
                                  deepcopy(self.scope)))
        self.parents[node.rexpr] = (node, 1)
        self.locations.append(Loc(node.rexpr, node, 1, self.depth,
                                  deepcopy(self.scope)))

    def visit_logical_expr(self, node):
        prev_depth = self.depth
        self.depth += 1
        super().visit_logical_expr(node)
        self.depth = prev_depth
        self.parents[node.lexpr] = (node, 0)
        self.locations.append(Loc(node.lexpr, node, 0, self.depth,
                                  deepcopy(self.scope)))
        self.parents[node.rexpr] = (node, 1)
        self.locations.append(Loc(node.rexpr, node, 1, self.depth,
                                  deepcopy(self.scope)))

    def visit_equality_expr(self, node):
        prev_depth = self.depth
        self.depth += 1
        super().visit_equality_expr(node)
        self.depth = prev_depth
        self.parents[node.lexpr] = (node, 0)
        self.parents[node.rexpr] = (node, 1)

    def visit_comparison_expr(self, node):
        prev_depth = self.depth
        self.depth += 1
        super().visit_comparison_expr(node)
        self.depth = prev_depth
        self.parents[node.lexpr] = (node, 0)
        self.locations.append(Loc(node.lexpr, node, 0, self.depth,
                                  deepcopy(self.scope)))
        self.parents[node.rexpr] = (node, 1)
        self.locations.append(Loc(node.rexpr, node, 1, self.depth,
                                  deepcopy(self.scope)))

    def visit_arith_expr(self, node):
        prev_depth = self.depth
        self.depth += 1
        super().visit_arith_expr(node)
        self.depth = prev_depth
        self.parents[node.lexpr] = (node, 0)
        self.locations.append(Loc(node.lexpr, node, 0, self.depth,
                                  deepcopy(self.scope)))
        self.parents[node.rexpr] = (node, 1)
        self.locations.append(Loc(node.rexpr, node, 1, self.depth,
                                  deepcopy(self.scope)))

    def visit_conditional(self, node):
        prev_depth = self.depth
        self.depth += 1
        super().visit_conditional(node)
        self.depth = prev_depth
        self.parents[node.cond] = (node, 0)
        self.locations.append(Loc(node.cond, node, 0, self.depth,
                                  deepcopy(self.scope)))
        if isinstance(node.true_branch, ast.Expr):
            self.parents[node.true_branch] = (node, 1)
            self.locations.append(Loc(node.true_branch, node, 1, self.depth,
                                      deepcopy(self.scope)))
        if isinstance(node.false_branch, ast.Expr):
            self.parents[node.false_branch] = (node, 2)
            self.locations.append(Loc(node.false_branch, node, 2, self.depth,
                                      deepcopy(self.scope)))

    def visit_multiconditional(self, node):
        prev_depth = self.depth
        self.depth += 1
        super().visit_multiconditional(node)
        self.depth = prev_depth
        i = 0
        if node.root_cond is not None:
            i += 1
        i = i + len(node.conditions)
        for j, c in enumerate(node.branches):
            self.parents[c] = (node, i + j)
            self.locations.append(Loc(c, node, i + j, self.depth,
                                      deepcopy(self.scope)))

    def visit_new(self, node):
        prev_depth = self.depth
        self.depth += 1
        super().visit_new(node)
        self.depth = prev_depth
        for i, e in enumerate(node.args):
            self.parents[e.expr] = (node, i)
            self.locations.append(Loc(e.expr, node, i, self.depth,
                                      deepcopy(self.scope)))

    def visit_field_access(self, node):
        prev_depth = self.depth
        self.depth += 1
        super().visit_field_access(node)
        self.depth = prev_depth
        if node.expr:
            self.parents[node.expr] = (node, 0)
            self.locations.append(Loc(node.expr, node, 0, self.depth,
                                      deepcopy(self.scope)))

    def visit_func_call(self, node):
        prev_depth = self.depth
        self.depth += 1
        super().visit_func_call(node)
        self.depth = prev_depth
        if node.receiver:
            self.parents[node.receiver] = (node, -1)
            self.locations.append(Loc(node.receiver, node, -1, self.depth,
                                      deepcopy(self.scope)))
        for i, p in enumerate(node.args):
            if isinstance(p, ast.CallArgument):
                self.parents[p.expr] = (node, i)
                self.locations.append(Loc(p.expr, node, i, self.depth,
                                          deepcopy(self.scope)))
            else:
                self.parents[p] = (node, i)
                self.locations.append(Loc(p, node, i, self.depth,
                                          deepcopy(self.scope)))

    def visit_assign(self, node):
        prev_depth = self.depth
        self.depth += 1
        super().visit_assign(node)
        self.depth = prev_depth
        if node.receiver:
            self.parents[node.receiver] = (node, -1)
            self.locations.append(Loc(node.receiver, node, -1, self.depth,
                                      deepcopy(self.scope)))
        self.parents[node.expr] = (node, 0)
        self.locations.append(Loc(node.expr, node, 0, self.depth,
                                  deepcopy(self.scope)))

    def visit_trycatch(self, node):
        prev_depth = self.depth
        self.depth += 1
        super().visit_trycatch(node)
        self.depth = prev_depth
        self.parents[node.try_block] = (node, 0)
        for i, catch_block in enumerate(node.catch_blocks.values()):
            self.parents[catch_block] = (node, i + 1)

    def get_parents(self, node: ast.Node):
        parents = []
        while node in self.parents:
            parent = self.parents[node]
            parents.append(parent)
            node = parent
        return parents
