import os
import subprocess
import logging
from pathlib import Path
from typing import Optional
from warnings import warn

import typer
import ast
import astor

from pbg.transformers import ProcessTransformer, StepTransformer, InitTransformer, ImportTransformer


logger: logging.Logger = logging.getLogger(__name__)

cli: typer.Typer = typer.Typer()


def transform_file(input_file: Path, output_file: Path, transformer: ProcessTransformer | StepTransformer) -> Path:
    """Transforms an existing Vivarium1.0-style `Process` implementation into a `process-bigraph`-compliant format, saved to `output_file`.

    :param input_file: Path to the input python file
    :param output_file: Path to the output python file
    :param transformer: Transformer to be applied
    """
    source_code = input_file.read_text()
    tree = ast.parse(source_code)

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
        typer.echo(f"Formatted {output_file} using Black")
    except subprocess.CalledProcessError:
        warn("Black formatting failed. File is still generated but not auto-formatted.")

    return output_file


@cli.command()
def process(input_file: Path, output_file: Path):
    try:
        transformer = ProcessTransformer()
        result = transform_file(input_file, output_file, transformer)
        typer.echo(f'Successfully migrated process to location: {output_file}')
    except Exception as e:
        msg = f"An error occurred: {e}"
        typer.echo(msg, err=True)
        logger.error(msg)


@cli.command()
def processes(input_dir: Path, output_dir: Path, suffix: str = "converted"):
    for root, _, dir_files in os.walk(input_dir):
        for dir_file in dir_files:
            if not dir_file.startswith("__"):
                fname = dir_file.split('.')[0] + f'_{suffix}.py'
                input_fp = Path(os.path.join(root, dir_file))
                output_fp = Path(os.path.join(output_dir, fname))
                typer.echo(f"Processing {input_fp}...")
                process(input_fp, output_fp)


@cli.command()
def step(input_file: Path, output_file: Path):
    try:
        transformer = StepTransformer()
        result = transform_file(input_file, output_file, transformer)
        typer.echo(result)
    except Exception as e:
        msg = f"An error occurred: {e}"
        typer.echo(msg, err=True)
        logger.error(msg)


@cli.command()
def test(*args: Path):
    for arg in args:
        typer.echo(arg)


if __name__ == "__main__":
    cli()
