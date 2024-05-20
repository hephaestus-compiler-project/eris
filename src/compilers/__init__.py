import os
import tempfile

from src import utils
from src.compilers.kotlin import KotlinCompiler
from src.compilers.groovy import GroovyCompiler
from src.compilers.java import JavaCompiler
from src.compilers.scala import ScalaCompiler
from src.ir import ast
from src.translators import TRANSLATORS


COMPILERS = {
    'kotlin': KotlinCompiler,
    'groovy': GroovyCompiler,
    'java': JavaCompiler,
    'scala': ScalaCompiler
}


def compile_program(language: str, program: ast.Program,
                    package_name: str, library_path: str):
    """
    Translate and compile the given program in the specified target language.
    """
    from src.args import args as cli_args
    filter_patterns = utils.path2set(cli_args.error_filter_patterns)
    # Create a temporary directory
    tmpdir = tempfile.mkdtemp()
    translator = TRANSLATORS[language](package=package_name)
    # Translate the program
    program_str = utils.translate_program(translator, program)
    segs = tuple(package_name.split("."))
    dst_file = os.path.join(tmpdir, *segs,
                            translator.get_filename())
    dst_dir = os.path.dirname(dst_file)
    utils.mkdir(dst_dir)
    utils.save_text(dst_file, program_str)
    compiler = COMPILERS[language](os.path.dirname(dst_dir),
                                   filter_patterns=filter_patterns,
                                   library_path=library_path)
    command_args = compiler.get_compiler_cmd()
    return utils.run_command(command_args, envs={"JAVA_OPTS": "-Xmx8g"})
