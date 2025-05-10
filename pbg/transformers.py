import ast
import logging
from typing import Optional


logger: logging.Logger = logging.getLogger(__name__)


class ModuleTransformer(ast.NodeTransformer):
    original_ancestor = "Process"
    new_ancestor = "BaseProcess"
    new_param = "core"


class InheritanceTransformer(ModuleTransformer):
    """Transforms class inheritance from vivarium.core.process.Process to BaseProcess."""
    def __init__(self, scope: Optional[str] = None):
        if scope is not None:
            self.original_ancestor = scope
            self.new_ancestor = f"Base{scope}"

    def visit_ClassDef(self, node):
        for base in node.bases:
            if isinstance(base, ast.Name) and base.id == self.original_ancestor:
                base.id = self.new_ancestor
                logger.info(
                    f"Updated class '{node.name}' inheritance from Vivarium Core ('1.0') Process to BaseProcess"
                )
        return node


class ProcessTransformer(InheritanceTransformer):
    def __init__(self):
        super().__init__(scope="Process")


class StepTransformer(InheritanceTransformer):
    def __init__(self):
        super().__init__(scope="Step")


class InitTransformer(ModuleTransformer):
    """Adds a new parameter to the constructor and modifies super().__init__ calls"""

    def visit_FunctionDef(self, node):
        if node.name == "__init__":
            param_names = {arg.arg for arg in node.args.args}

            if self.new_param not in param_names:
                new_arg = ast.arg(arg=self.new_param, annotation=None)
                node.args.args.append(new_arg)  # Add 'core' to the parameter list
                logger.info(f"Added '{self.new_param}' parameter to __init__ in class.")

            for stmt in node.body:
                if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
                    if (
                        isinstance(stmt.value.func, ast.Attribute)
                        and stmt.value.func.attr == "__init__"
                    ):
                        if not any(
                            isinstance(arg, ast.Name) and arg.id == self.new_param
                            for arg in stmt.value.args
                        ):
                            stmt.value.args.append(
                                ast.Name(id=self.new_param, ctx=ast.Load())
                            )
                            logger.info(
                                f"Modified super().__init__ call to pass '{self.new_param}'."
                            )

        return node


class ImportTransformer(ModuleTransformer):
    """Adds `from pbg.data_model.base_process import BaseProcess, CORE` to the top of the new module"""

    def __init__(self):
        super().__init__()
        self.import_found = False

    def visit_ImportFrom(self, node):
        if node.module == "pbg.data_model.base_process" and self.new_ancestor in [
            n.name for n in node.names
        ]:
            self.import_found = True
        return node

    def add_import(self, tree):
        if not self.import_found:
            new_import = ast.ImportFrom(
                module="pbg.data_model.base_process",
                names=[
                    ast.alias(name=self.new_ancestor, asname=None),
                    # ast.alias(name="CORE", asname=None),
                ],
                level=0,
            )
            tree.body.insert(0, new_import)
            logger.info(
                "Added import: from pbg.data_model.base_process import BaseProcess, CORE"
            )