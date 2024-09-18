import re
import os

from src.compilers.base import BaseCompiler


class JavaCompiler(BaseCompiler):
    # Match (example.groovy):(error message until empty line)
    ERROR_REGEX = re.compile(
        r'([a-zA-Z0-9\/_]+.java):(\d+:[ ]+error:[ ]+.*)(.*?(?=\n{1,}))')

    CRASH_REGEX = re.compile(r'.*(at jdk\.)(.*)')

    def __init__(self, input_name, filter_patterns=None,
                 extra_options=None):
        input_name = os.path.join(input_name, '*', '*.java')
        super().__init__(input_name, filter_patterns, extra_options)

    @classmethod
    def get_compiler_version(cls):
        return ['javac', '-version']

    def get_compiler_cmd(self):
        return [
            'javac',
            '-nowarn',
            '-J--add-exports=jdk.compiler/com.sun.tools.javac.api=ALL-UNNAMED',
            '-J--add-exports=jdk.compiler/com.sun.tools.javac.code=ALL-UNNAMED',
            '-J--add-exports=jdk.compiler/com.sun.tools.javac.file=ALL-UNNAMED',
            '-J--add-exports=jdk.compiler/com.sun.tools.javac.main=ALL-UNNAMED',
            '-J--add-exports=jdk.compiler/com.sun.tools.javac.model=ALL-UNNAMED',
            '-J--add-exports=jdk.compiler/com.sun.tools.javac.processing=ALL-UNNAMED',
            '-J--add-exports=jdk.compiler/com.sun.tools.javac.tree=ALL-UNNAMED',
            '-J--add-exports=jdk.compiler/com.sun.tools.javac.util=ALL-UNNAMED',
            '-J--add-opens=jdk.compiler/com.sun.tools.javac.comp=ALL-UNNAMED',
        ] + self.extra_options + [self.input_name]

    def get_filename(self, match):
        return match[0]

    def get_error_msg(self, match):
        return match[1]
