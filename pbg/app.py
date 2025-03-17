import subprocess
from warnings import warn

import typer
import ast
import astor
from pathlib import Path


app = typer.Typer()


class ModuleTransformer(ast.NodeTransformer):
    original_ancestor = "Process"
    new_ancestor = "BaseProcess"
    new_param = "core"


class InheritanceTransformer(ModuleTransformer):
    """Transforms class inheritance from OldProcess to BaseProcess."""

    def visit_ClassDef(self, node):
        """Modify class inheritance from OldProcess -> BaseProcess"""
        for base in node.bases:
            if isinstance(base, ast.Name) and base.id == self.original_ancestor:
                base.id = self.new_ancestor
                print(
                    f"Updated class '{node.name}' inheritance from OldProcess to BaseProcess"
                )
        return node


class InitTransformer(ModuleTransformer):
    """Adds a new parameter to the constructor and modifies super().__init__ calls"""

    def visit_FunctionDef(self, node):
        """Modify the __init__ method to add the 'core' parameter and pass it to super()"""
        if node.name == "__init__":
            # Ensure 'core' parameter exists
            param_names = {arg.arg for arg in node.args.args}
            if self.new_param not in param_names:
                new_arg = ast.arg(arg=self.new_param, annotation=None)
                node.args.args.append(new_arg)  # Add 'core' to the parameter list
                print(f"Added '{self.new_param}' parameter to __init__ in class.")

            # Modify super().__init__ call to include 'core'
            for stmt in node.body:
                if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
                    if (
                        isinstance(stmt.value.func, ast.Attribute)
                        and stmt.value.func.attr == "__init__"
                    ):
                        # Check if 'core' is already passed
                        if not any(
                            isinstance(arg, ast.Name) and arg.id == self.new_param
                            for arg in stmt.value.args
                        ):
                            stmt.value.args.append(
                                ast.Name(id=self.new_param, ctx=ast.Load())
                            )  # Pass 'core' to super()
                            print(
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
                    ast.alias(name="CORE", asname=None),
                ],
                level=0,
            )
            tree.body.insert(0, new_import)
            print(
                "Added import: from pbg.data_model.base_process import BaseProcess, CORE"
            )


def transform_python_file(input_file: Path, output_file: Path):
    """
    Transform an existing Vivarium1.0-style `Process` implementation into a `process-bigraph`-compliant format, saved to `output_file`.

    :param input_file: Path to the input python file
    :param output_file: Path to the output python file
    """
    source_code = input_file.read_text()
    tree = ast.parse(source_code)

    transformer = InheritanceTransformer()
    transformed_tree = transformer.visit(tree)

    init_transformer = InitTransformer()
    transformed_tree = init_transformer.visit(transformed_tree)

    import_checker = ImportTransformer()
    transformed_tree = import_checker.visit(transformed_tree)
    import_checker.add_import(transformed_tree)

    new_code = astor.to_source(transformed_tree)

    output_file.write_text(new_code)

    try:
        subprocess.run(["black", output_file], check=True)
        print(f"Formatted {output_file} using Black")
    except subprocess.CalledProcessError:
        warn("Black formatting failed. File is still generated but not auto-formatted.")

    return output_file


@app.command()
def process(input_file: Path, output_file: Path):
    try:
        result = transform_python_file(input_file, output_file)
        typer.echo(result)
    except Exception as e:
        typer.echo(f"Error: {e}", err=True)


@app.command()
def test(*args: Path):
    for arg in args:
        typer.echo(arg)


if __name__ == "__main__":
    app()
