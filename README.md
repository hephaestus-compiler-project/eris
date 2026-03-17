eris
====

> **Note:** This repository ([eris](https://github.com/hephaestus-compiler-project/eris)) is a fork of [thalia](https://github.com/hephaestus-compiler-project/thalia), which is itself a fork of [hephaestus](https://github.com/hephaestus-compiler-project/hephaestus). In the near future, we plan to integrate all three repositories into a single unified repo under [hephaestus](https://github.com/hephaestus-compiler-project/hephaestus).

`eris` is a testing framework for detecting bugs in type analyzers via
*ill-typed program enumeration*.
The key idea is to take well-typed seed programs and
systematically enumerate ill-typed variants
by injecting type errors at every eligible location in the program.
Each variant contains exactly one injected error and should therefore
be rejected by the type analyzer under test.
If it is not, `eris` flags the variant as a potential bug.

`eris` builds on top of
[Hephaestus](https://github.com/hephaestus-compiler-project/hephaestus)
and is described in the PLDI'26 paper
*"Enumerating Ill-Typed Programs for Testing Type Analyzers"*.
Currently, `eris` supports programs written in four popular programming
languages: Java, Groovy, Kotlin, and Scala.


## Ill-Typed Program Enumeration

At a high level, `eris` works as follows.
The input is a well-typed seed program,
which serves as the skeleton for enumeration.
`eris` identifies all *injection locations* in the skeleton,
that is,
expressions whose type can be replaced with an incompatible one
without altering any other part of the program.
For each location, it enumerates a set of *replacement types*
that are type-incompatible with the expected type at that location,
yielding a collection of ill-typed variants.

To keep the number of variants tractable,
`eris` employs *type shape isomorphism*:
two types are considered isomorphic if they share the same
structural shape (e.g., both are generic containers with the same arity).
Instead of enumerating every incompatible type individually,
`eris` picks one representative per isomorphism class,
reducing the variant space while preserving coverage.

The seed programs themselves can be produced by one of `eris`'s built-in
generators, or supplied directly from disk via `--seeds`.

Finally, each ill-typed variant is compiled by the type analyzer under test.
If the type analyzer accepts a variant, `eris` records it as a potential bug.


# Requirements

* Python 3.8+


# Getting Started

## Install

```bash
python3 -m venv .env
source .env/bin/activate
pip install .
```

## Run tests

```bash
python -m pytest
```

The output of the previous command should be similar to the following:

```
tests/test_api_graph.py::test1 PASSED                                     [  0%]
tests/test_api_graph.py::test2 PASSED                                     [  0%]
...
tests/test_use_analysis.py::test_program6 PASSED                          [ 99%]
tests/test_use_analysis.py::test_program7 PASSED                          [ 99%]
tests/test_use_analysis.py::test_program8 PASSED                          [100%]

============================= 343 passed in 2.02s ==============================
```

## Usage

```
usage: eris [-h] [-g {base,api,api-decl,cfg}] [--api-doc-path API_DOC_PATH]
            [-s SECONDS] [-i ITERATIONS] [--api-rules API_RULES]
            [--max-conditional-depth MAX_CONDITIONAL_DEPTH] [--erase-types]
            [--inject-type-error] [--enable-expression-cache]
            [--path-search-strategy {shortest,ksimple}]
            [-t TRANSFORMATIONS] [--batch BATCH] [-b BUGS] [-n NAME]
            [-T [{TypeErasure} ...]] [--transformation-schedule SCHEDULE]
            [-R REPLAY] [-e] [-k] [-S] [-w WORKERS] [-d] [-r]
            [-F LOG_FILE] [-L] [-N]
            [--language {kotlin,groovy,java,scala}]
            [--max-type-params MAX_TYPE_PARAMS] [--max-depth MAX_DEPTH]
            [-P] [--timeout TIMEOUT] [--cast-numbers]
            [--disable-function-references] [--disable-use-site-variance]
            [--disable-contravariance-use-site]
            [--disable-bounded-type-parameters]
            [--disable-parameterized-functions] [--disable-sam]
            [--local-variable-prob LOCAL_VARIABLE_PROB]
            [--error-filter-patterns ERROR_FILTER_PATTERNS]
            [--error-enumerator {accessibility,type,flow-type,final-var}]
            [--max-cfg-nodes MAX_CFG_NODES]
            [--max-cfg-local-vars MAX_CFG_LOCAL_VARS]
            [--use-nullable-types] [--extra-compiler-option flag value]
            [--seeds SEEDS] [--ignore-locations-with-unknown-target-type]
            [--disable-location-cache] [--disable-enumeration]
            [--disable-type-isomorphism]

optional arguments:
  -h, --help            show this help message and exit
  -g {base,api,api-decl,cfg}, --generator {base,api,api-decl,cfg}
                        Type of generator
  --api-doc-path API_DOC_PATH
                        Path to API docs
  -s SECONDS, --seconds SECONDS
                        Timeout in seconds
  -i ITERATIONS, --iterations ITERATIONS
                        Iterations to run (default: 3)
  --api-rules API_RULES
                        File that contains the rules specifying the APIs used
                        for program enumeration (used only with API-based
                        program generation)
  --max-conditional-depth MAX_CONDITIONAL_DEPTH
                        Maximum depth of conditionals
  --erase-types         Erases types from the program while preserving its
                        semantics
  --inject-type-error   Injects a type error in the generated program
  --enable-expression-cache
                        Re-use expressions that yield certain types
  --path-search-strategy {shortest,ksimple}
                        Strategy for enumerating paths between two nodes
  -t TRANSFORMATIONS, --transformations TRANSFORMATIONS
                        Number of transformations in each round
  --batch BATCH         Number of programs to generate before invoking the
                        compiler
  -b BUGS, --bugs BUGS  Set bug directory (default: bugs)
  -n NAME, --name NAME  Set name of this testing instance (default: random
                        string)
  -T [{TypeErasure} ...], --transformation-types [{TypeErasure} ...]
                        Select specific transformations to perform
  --transformation-schedule TRANSFORMATION_SCHEDULE
                        A file containing the schedule of transformations
  -R REPLAY, --replay REPLAY
                        Give a program to use instead of a randomly generated
                        (pickled)
  -e, --examine         Open ipdb for a program (can be used only with
                        --replay option)
  -k, --keep-all        Save all programs
  -S, --print-stacktrace
                        When an error occurs print stack trace
  -w WORKERS, --workers WORKERS
                        Number of workers for processing test programs
  -d, --debug
  -r, --rerun           Run only the last transformation. If failed, start
                        from the last and go back until the transformation
                        introduces the error
  -F LOG_FILE, --log-file LOG_FILE
                        Set log file (default: logs)
  -L, --log             Keep logs for each transformation (bugs/session/logs)
  -N, --dry-run         Do not compile the programs
  --language {kotlin,groovy,java,scala}
                        Select specific language
  --max-type-params MAX_TYPE_PARAMS
                        Maximum number of type parameters to generate
  --max-depth MAX_DEPTH
                        Generate programs up to the given depth
  -P, --only-correctness-preserving-transformations
                        Use only correctness-preserving transformations
  --timeout TIMEOUT     Timeout for transformations (in seconds)
  --cast-numbers        Cast numeric constants to their actual type (this
                        option is used to avoid re-occurrence of a specific
                        Groovy bug)
  --disable-function-references
                        Disable function references
  --disable-use-site-variance
                        Disable use-site variance
  --disable-contravariance-use-site
                        Disable contravariance in use-site variance
  --disable-bounded-type-parameters
                        Disable bounded type parameters
  --disable-parameterized-functions
                        Disable parameterized functions
  --disable-sam         Disable SAM coercions
  --local-variable-prob LOCAL_VARIABLE_PROB
                        Probability of assigning an expression to a local
                        variable
  --error-filter-patterns ERROR_FILTER_PATTERNS
                        A file containing regular expressions for filtering
                        compiler error messages
  --error-enumerator {accessibility,type,flow-type,final-var}
                        Select a strategy for enumerating errors in a given
                        program
  --max-cfg-nodes MAX_CFG_NODES
                        Maximum nodes in CFG graph (only applicable when
                        --generator cfg)
  --max-cfg-local-vars MAX_CFG_LOCAL_VARS
                        Maximum local variables per CFG block (only applicable
                        when --generator cfg)
  --use-nullable-types  Use nullable types in the generated programs and
                        enumerators
  --extra-compiler-option flag value
                        Extra compiler options for invoking the compiler
  --seeds SEEDS         Directory of seeds
  --ignore-locations-with-unknown-target-type
                        Disregard locations whose expected type is unknown
  --disable-location-cache
                        Disable cache for locations. Every location is now
                        considered distinct.
  --disable-enumeration
                        Disable enumeration and output only statistics
  --disable-type-isomorphism
                        Disable type isomorphism and perform full error
                        enumeration
```

## Example: Testing the Groovy Compiler

We provide an example that demonstrates the `eris` testing framework.
In this example, we use `eris` to enumerate 30 ill-typed Groovy variants
from a set of well-typed seed programs found in
`example-apis/groovy-stdlib/example-seeds/`.

```bash
eris --language groovy \
  --transformations 0 \
  --batch 1 -i 30 -P \
  --max-depth 2 \
  --generator api-decl \
  --api-doc-path example-apis/java-stdlib/json-docs \
  --keep-all \
  --name groovy-session \
  --error-enumerator type -L
```

The expected output is similar to:

```
stop_cond             iterations (30)
transformations       0
transformation_types  TypeErasure
bugs                  bugs
name                  groovy-session
language              groovy
compiler              Groovy compiler version 5.0.4
Copyright 2003-2025 The Apache Software Foundation. https://groovy-lang.org/
========================================================================
Test Programs Passed 30 / 30 ✔          Test Programs Failed 0 / 30 ✘
Total faults: 0
```

The `bugs/groovy-session/` directory contains,
among other things, two files: `stats.json` and `faults.json`.

`stats.json` contains statistics about the testing session:

```json
{
  "Info": {
    "stop_cond": "iterations",
    "stop_cond_value": 30,
    "transformations": 0,
    "transformation_types": "TypeErasure",
    "bugs": "bugs",
    "name": "groovy-session",
    "language": "groovy",
    "generator": "api-decl",
    "erase_types": false,
    "inject_type_error": false,
    "compiler": "Groovy compiler version 5.0.4\n..."
  },
  "totals": {
    "passed": 30,
    "failed": 0
  },
  "synthesis_time": 4.44,
  "compilation_time": 50.84
}
```

In this example, `faults.json` is empty.
If `eris` had detected a bug, `faults.json` would contain an entry like:

```json
{
  "2": {
    "transformations": [],
    "error": "SHOULD NOT BE COMPILED: Added type error using TypeErrorEnumerator:\n - Expected type: Integer[]\n - Actual type: java.security.cert.X509CRL[]\n - Previous expression: new Integer[]{-85, 49, 28}\n - New expression: new java.security.cert.X509CRL[0]\n - Receiver location: False\n",
    "programs": {
      "/tmp/tmpqfzn6sc8/src/dunedin/Main.groovy": false
    },
    "time": 0.44
  }
}
```

When a bug is found, `eris` stores the bug-revealing program inside
`bugs/groovy-session/`.
The `--keep-all` flag additionally saves every generated program
(including non-bug-triggering ones) in `bugs/groovy-session/generator/`.

### Logging

The `-L` flag enables logging.
The injected errors are recorded in
`bugs/groovy-session/logs/error-enumeration.logs`.
Its contents look like:

```
Enumerating error program 1 for skeleton 1

API namespace: iter_1
Added type error using TypeErrorEnumerator:
 - Expected type: Integer[]
 - Actual type: Integer[][]
 - Previous expression new Integer[]{-85, 49, 28}
 - New expression new Integer[0][0]
 - Receiver location: False

Enumerating error program 2 for skeleton 1

API namespace: iter_1
Added type error using TypeErrorEnumerator:
 - Expected type: Integer[]
 - Actual type: java.security.cert.X509CRL[]
 - Previous expression new Integer[]{-85, 49, 28}
 - New expression new java.security.cert.X509CRL[0]
 - Receiver location: False
...
```

Each entry shows the location of the injected error,
the expected and actual types, and the replacement expression.


# Generators

`eris` includes four seed generators for producing well-typed skeleton programs:

| Generator | Description |
|-----------|-------------|
| `base` | Synthesizes programs from scratch using a built-in type hierarchy |
| `api` | API-driven synthesis: enumerates paths in an API graph to construct call sequences |
| `api-decl` | Like `api`, but builds programs that implement an API declaration |
| `cfg` | Control-flow-graph-based synthesis |

The `api`, `api-decl`, and `cfg` generators require an API specification
provided via `--api-doc-path`.
Example API specifications for several standard libraries are provided
under `example-apis/`.

Alternatively, you can supply pre-built seeds directly via `--seeds`,
skipping generation entirely.

### Generating Seeds

To generate 30 well-typed Groovy seed programs using the `api-decl` generator:

```bash
eris --language groovy \
  --transformations 0 \
  --batch 1 -i 30 -P \
  --max-depth 2 \
  --generator api-decl \
  --api-doc-path example-apis/java-stdlib/json-docs \
  --keep-all \
  --name groovy-seeds \
  --dry-run
```

The `--dry-run` flag skips compilation and only writes the program files.
The generated programs are saved in `bugs/groovy-seeds/generator/`.

You can then use this directory as input to a subsequent enumeration run
via `--seeds bugs/groovy-seeds/generator`.


# Supported Languages

Currently, `eris` generates programs written in four popular programming
languages: Java, Groovy, Kotlin, and Scala.
Use the `--language` option to specify the target language.

To support a new language, you need to implement the following:

* A translator that converts a program written in the IR into a source file
  in the target language.
  Extend the
  [src.translators.base.BaseTranslator](src/translators/base.py) class.

* A compiler wrapper that reads compiler output and distinguishes crashes
  from diagnostic errors.
  Extend the
  [src.compilers.base.BaseCompiler](src/compilers/base.py) class.

* (Optionally) Built-in types for the language — see
  [src/ir/java_types.py](src/ir/java_types.py) for guidance.


# Related Publications

* Thodoris Sotiropoulos, Stefanos Chaliasos, Zhendong Su.
  *Enumerating Ill-Typed Programs for Testing Type Analyzers.*
  PLDI '26. ACM, 2026 **(conditionally accepted)**.

* Thodoris Sotiropoulos, Stefanos Chaliasos, Zhendong Su.
  [API-driven Program Synthesis for Testing Static Typing Implementations](https://theosotr.github.io/assets/pdf/popl24.pdf).
  POPL '24. ACM, January 2024.

* Stefanos Chaliasos, Thodoris Sotiropoulos, Diomidis Spinellis, Arthur Gervais,
  Benjamin Livshits, and Dimitris Mitropoulos.
  [Finding Typing Compiler Bugs](https://doi.org/10.1145/3519939.3523427).
  PLDI '22. ACM, June 2022.

* Stefanos Chaliasos, Thodoris Sotiropoulos, Georgios-Petros Drosos,
  Charalambos Mitropoulos, Dimitris Mitropoulos, and Diomidis Spinellis.
  [Well-typed programs can go wrong: A study of typing-related bugs in JVM compilers](https://doi.org/10.1145/3485500).
  OOPSLA '21. ACM, October 2021.


# Related Artifacts

* [Replication Package for Article: Enumerating Ill-Typed Programs for Testing Type Analyzers](https://zenodo.org/records/) PLDI 2026 software.
* [Replication Package for Article: API-driven Program Synthesis for Testing Static Typing Implementations](https://zenodo.org/records/8425071) November 2023 software.
* [Replication Package for Article: Finding Typing Compiler Bugs](https://zenodo.org/record/6410434) March 2022 software.
* [Replication Package for Article: "Well-Typed Programs Can Go Wrong: A Study of Typing-Related Bugs in JVM Compilers"](https://doi.org/10.5281/zenodo.5411667) October 2021 software.
