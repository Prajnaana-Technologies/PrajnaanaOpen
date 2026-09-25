# Copyright (c) 2026 Prajnaana Technologies Pvt. Ltd.
# Original author: Dhanya Shree S
# SPDX-License-Identifier: MIT
#
# Licensed under the MIT License. See LICENSE in the project root.

"""Rules a generated test must satisfy before it is allowed to count.

The failure this exists to prevent: a generated test that asserts nothing
passes forever, and gets counted as coverage. That is worse than having no
test, because a gap you know about gets filled and a gap you believe is
covered does not.

Two mechanisms:

  * ACCEPTANCE RULES -- mechanical checks on the generated module. Every test
    must assert or skip, none may reach the network, none may loop forever.
    These run on every generation and cost nothing.

  * APPROVAL -- a recorded human decision tied to the module's test code,
    with the run's METRICS line excluded. Editing anything else in the
    module invalidates the approval, because the fingerprint changes.

Approval is opt-in via HA_REQUIRE_APPROVAL=1. Unset, generation reports rule
violations and proceeds; that is the right default for a bench, and the wrong
one for anything whose results get quoted.

    python -m regression.governance validate
    python -m regression.governance approve --by "your name"
    python -m regression.governance status

validate, approve and status each act on every generated module a run
would execute -- the planner-selected one and the requirement suite.
--path narrows any of them to a single file, and is accepted on either
side of the subcommand:

    python -m regression.governance --path PATH approve --by "your name"
    python -m regression.governance approve --by "your name" --path PATH

Both orders parse, so the command a refusal prints runs exactly as it is
printed.
"""

import ast
import hashlib
import json
import os
from datetime import datetime, timezone

from regression.paths import (
    CANONICAL_TEST_PATH,
    GENERATED_TEST_PATH,
    in_base,
)

# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------

# A test's position in its life. The point of naming these is that "generated"
# and "trusted" stop being the same state by default.
DRAFT = "draft"                # generated, not yet checked
REVIEWED = "reviewed"          # passed the acceptance rules
APPROVED = "approved"          # a person signed off on this test code
ACTIVE = "active"              # running as part of regression
QUARANTINED = "quarantined"    # failing unreliably; runs but does not gate
DEPRECATED = "deprecated"      # retired, kept for history

LIFECYCLE = (DRAFT, REVIEWED, APPROVED, ACTIVE, QUARANTINED, DEPRECATED)

# Which transitions are legal. Anything else is a bug in the caller, not a
# state to be silently accepted.
TRANSITIONS = {
    DRAFT: (REVIEWED, DEPRECATED),
    REVIEWED: (APPROVED, DRAFT, DEPRECATED),
    APPROVED: (ACTIVE, DRAFT, DEPRECATED),
    ACTIVE: (QUARANTINED, DEPRECATED, DRAFT),
    QUARANTINED: (ACTIVE, DEPRECATED),
    DEPRECATED: (),
}

REQUIRE_APPROVAL_ENV = "HA_REQUIRE_APPROVAL"

RECORD_PATH = os.path.join("regression", "artifacts", "governance", "approvals.json")


def can_transition(current, target):
    """True when moving from one lifecycle state to another is allowed."""
    if current not in LIFECYCLE:
        raise ValueError("unknown state: {}".format(current))

    if target not in LIFECYCLE:
        raise ValueError("unknown state: {}".format(target))

    return target in TRANSITIONS[current]


# ---------------------------------------------------------------------------
# Acceptance rules
# ---------------------------------------------------------------------------

# Modules a generated test has no business importing. Reaching the network
# from a regression test makes it non-deterministic and dependent on
# something nobody controls.
FORBIDDEN_IMPORTS = (
    "subprocess",
    "shutil",
    "ctypes",
    "socket",
    "requests",
    "urllib",
    "urllib3",
    "http",
    "ftplib",
    "telnetlib",
    "smtplib",
)

# Calls that let a test reach outside the harness entirely. Matched on the
# bare name, so os.system and system() are both caught.
#
# A short list here leaves a generated test free to start a process
# (subprocess.run), delete a tree (shutil.rmtree) or import by name
# (__import__("shutil")), so the list covers each of those. Names the
# generated modules use legitimately are deliberately absent: "run" is the
# emitted helper that drives a coroutine, and "open" is how a scenario reads
# a wave file.
FORBIDDEN_CALLS = (
    "system",       # os.system
    "popen",        # os.popen
    "check_output", # subprocess
    "check_call",   # subprocess
    "Popen",        # subprocess
    "eval",
    "exec",
    "compile",
    "__import__",
    "rmtree",       # shutil
    "remove",       # os
    "unlink",       # os, pathlib
    "rename",       # os
    "chmod",        # os
    "globals",      # hands back the module namespace itself
    "vars",         # the same namespace, or any object's
    "locals",       # the frame's namespace, which a comprehension can edit
)

# Names that hand back a namespace, which is every other rule's blind spot:
# the rules read a call by how it is WRITTEN, and globals()["__import__"] is
# a name that appears nowhere. Refused wherever they are read, not only
# where they are called, because `g = globals` writes the call on the next
# line under a spelling nobody listed. They sit in FORBIDDEN_CALLS as well,
# which is what puts them in PROTECTED_NAMES.
NAMESPACE_NAMES = ("globals", "vars", "locals")


# What a generated module is allowed to do when it is imported.
#
# A generated module is a header, its imports, its constants and its test
# functions. Anything else at module level runs the moment pytest collects
# the file, before a single rule about the tests themselves has been
# consulted.
#
# This matters because the generators build the header by formatting text
# into it -- the source document's name, the firmware build id -- and a
# newline in one of those fields ends the "#" comment it is pasted into and
# starts a statement. A source name ending in a newline followed by
# PWNED = print("owned") is therefore a module that prints on import, and
# only a rule about what sits between the functions can see it.
#
# The allowed shapes are the ones the templates emit: a docstring,
# imports, defs and classes, and assignments of constants to plain names --
# METRICS, THRESHOLDS, the limits and the intensity arithmetic. Everything
# else at module level is a violation.
TOP_LEVEL_STATEMENTS = (
    ast.Import,
    ast.ImportFrom,
    ast.FunctionDef,
    ast.AsyncFunctionDef,
    ast.ClassDef,
    ast.Assign,
    ast.AnnAssign,
)

# Calls a module-level constant may be built with. The templates need
# arithmetic on a literal and one environment variable -- USE_SAMPLES is
# int(float(os.getenv("HA_TEST_SECONDS", "0.125")) * 16000) and the stream
# counts are max(3, PACKETS // 8). Nothing here reads a file, writes one or
# prints, so a constant whose value calls anything else is not a constant.
# Spelled as the call appears: "os.getenv", not "getenv". A bare getenv is
# a call to whatever the module has bound that name to, which is not the
# same thing as reading the environment.
TOP_LEVEL_CALLS = (
    "int", "float", "str", "bool", "len", "max", "min", "abs", "round",
    "os.getenv",
)

# The only decorator a generated module may carry. A decorator is a call that
# runs the moment pytest imports the file, so an arbitrary one is top-level
# code wearing an "@": @(lambda f: pathlib.Path("P").touch() or f) touches
# the file while pytest collects it. The templates emit exactly
# @pytest.mark.<name>(...) and nothing else.
ALLOWED_DECORATOR = ("pytest", "mark")

# The modules a generated module may import, each with the ONE name it may
# be bound to. An allowlist, not a list of refusals: a refusal covers only
# the spellings somebody thought of, and Python offers more ways to bind a
# name than such a list holds -- an alias, a star import, a dunder. What is
# not named here is refused for not being named.
#
# The templates need exactly these: pytest for the marks and the skips,
# numpy as np for the sample arithmetic, os for one environment read, time
# and asyncio for the waits, wave for the audio file, and the regression
# package for the measurement helpers.
ALLOWED_IMPORTS = {
    "pytest": "pytest",
    "numpy": "np",
    "os": "os",
    "time": "time",
    "asyncio": "asyncio",
    "wave": "wave",
    "regression": "regression",
}

# Of the imports above, the ones whose surface reaches the operating system,
# and everything a generated test may use from each. Naming the escapes
# instead covers only the ones already named: past os.system and os.popen
# sit os.posix_spawn, os.spawnv and os.startfile, and naming those three
# leaves the fourth. The templates use only what is listed here, so
# anything else in these modules is a spelling no generator produces.
#
# numpy and pytest are absent deliberately: neither starts a process nor
# writes outside the test, and listing every array function the templates
# use would go stale on the first new scenario.
GUARDED_MODULES = {
    "os": ("getenv",),
    "asyncio": ("ensure_future", "gather", "get_event_loop",
                "new_event_loop", "set_event_loop", "sleep", "wait_for"),
    "time": ("monotonic", "perf_counter", "sleep", "time"),
    "wave": ("open",),
}

# Module names that reach the operating system, the process table or the
# interpreter's own machinery. GUARDED_MODULES asks what an attribute is
# taken FROM; this asks what the chain NAMES, at any depth, because a
# module is reachable as an attribute of anything that imports it:
# np.sys is the sys module, np.ctypeslib.ctypes is ctypes, and every module
# in this package imports os, so regression.paths.os is os under a spelling
# the import allowlist never sees. os is here too, and the one chain it may
# head -- os.getenv -- is decided by GUARDED_MODULES instead.
SYSTEM_MODULES = frozenset(FORBIDDEN_IMPORTS) | frozenset((
    "os",
    "sys",
    "ctypeslib",    # numpy's re-export of ctypes
    "builtins",
    "importlib",
))

# The packages a generated module imports whole, and what a test may reach
# through each. numpy and the regression package both carry their own
# imports as attributes, so naming the module is not the same as naming its
# surface: without a list here, np reaches sys, ctypes and every os function
# through np.lib, and regression reaches subprocess through any of its
# modules that imports it.
#
# Each entry is (names allowed as the first attribute, longest chain
# allowed). Both lists are what the two generators emit -- np.<function> for
# the sample arithmetic, and the regression modules the templates import --
# so a template that reaches for a new helper adds it here.
PACKAGE_SURFACE = {
    "np": (("abs", "array", "concatenate", "float32", "frombuffer", "int16",
            "linspace", "log10", "mean", "pi", "sign", "sin", "sqrt", "sum"),
           2),
    "regression": (("audio_prep", "measurements"), 3),
}

# Names every rule above decides by, which the module must therefore not be
# allowed to define. The rules match what a callee is CALLED, and in Python
# the module says what a name means: "from os import system as int" makes
# int() a shell, and int() is on the allowed list, while rule 5 matches the
# local name and finds no "system" anywhere. "def str(x): print(...)" and
# "int = print" do the same with no import at all. So an allowed spelling is
# allowed only while it still means what it says, and binding one of these
# names is itself the violation.
#
# Derived from the lists rather than typed out again, so a name added to any
# of them is protected without a second edit.
PROTECTED_NAMES = frozenset(
    set(FORBIDDEN_CALLS)
    | {spelling.split(".")[0] for spelling in TOP_LEVEL_CALLS}
    | {ALLOWED_DECORATOR[0]}
    | set(GUARDED_MODULES)
)


def _is_dunder(name):
    """True for a name Python or pytest may call without one being written.

    A module-level __getattr__ runs for any name the module does not define,
    and pytest looks several up on a module it has just collected. A class
    body's __init_subclass__ runs when anything subclasses it, and its
    __class_getitem__ runs on a subscript. Each of those is a body that
    executes at import with no call anywhere in the file, and there are more
    of them than a list of names keeps up with. The shape is the rule
    instead: a generated module binds no dunder at all, except __init__ on a
    class it defines.
    """
    return name.startswith("__") and name.endswith("__") and len(name) > 4


class Violation(object):
    """One broken acceptance rule."""

    def __init__(self, rule, detail, line=0):
        self.rule = rule
        self.detail = detail
        self.line = line

    def __str__(self):
        location = " (line {})".format(self.line) if self.line else ""

        return "{}: {}{}".format(self.rule, self.detail, location)

    def __repr__(self):
        return "Violation({!r}, {!r}, {!r})".format(
            self.rule, self.detail, self.line
        )


def _asserts_or_skips(node):
    """True when a test body contains an assert, a skip or a raises block.

    The `with` clause below counts ANY with-statement, not only
    `with pytest.raises(...)`: a test whose body is `with open(path):` and
    nothing else passes this rule while asserting nothing. That is a
    deliberately generous reading -- the rule exists to catch a generator
    emitting an empty test, not to police hand-written ones -- but it is
    weaker than "must be able to fail" sounds, so it is written down here
    rather than left to be discovered.
    """
    for child in ast.walk(node):
        if isinstance(child, ast.Assert):
            return True

        # pytest.skip(...) / pytest.fail(...) / pytest.xfail(...)
        if isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute):
            if child.func.attr in ("skip", "fail", "xfail", "raises"):
                return True

        # with pytest.raises(...): -- and, as above, any other with.
        if isinstance(child, ast.withitem):
            return True

    return False


def _is_docstring(node):
    return (isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str))


def _spelled(node):
    """How a callee is written, as far as matching it needs.

    "os.getenv" for an attribute of a plain name, the bare name for a plain
    name, and a placeholder for anything else -- a call on the result of a
    call, a subscript, a lambda. The placeholder never matches an allowed
    name, which is the right default for a spelling nobody anticipated.
    """
    if isinstance(node, ast.Attribute):
        if isinstance(node.value, ast.Name):
            return "{}.{}".format(node.value.id, node.attr)

        return "<expression>.{}".format(node.attr)

    if isinstance(node, ast.Name):
        return node.id

    return "<expression>"


def _called_names(node):
    """Every callee in an expression, spelled by _spelled()."""
    return [_spelled(child.func) for child in ast.walk(node)
            if isinstance(child, ast.Call)]


def _value_violations(node, where, line, class_names=()):
    """Rule 7 over one expression that is evaluated at import time.

    Every place an expression can hide in a module-level statement runs when
    pytest imports the file: the value of an assignment, its annotation, a
    def's decorators, its default arguments and its annotations, a class's
    bases. Checking the value alone leaves every one of the others free to
    run code: `def helper(x=print("Result: pass"))` prints a forged verdict
    line on stdout while the file is being collected.
    """
    violations = []

    for name in _called_names(node):
        if name in class_names:
            violations.append(Violation(
                "no_top_level_code",
                "calls {}() {}; a class this module defines runs code of "
                "the module's own, so calling one at import is the module "
                "running that code".format(name, where),
                line,
            ))
            continue

        if name not in TOP_LEVEL_CALLS:
            violations.append(Violation(
                "no_top_level_code",
                "calls {}() {}; what runs at import may only be a literal "
                "or arithmetic on one".format(name, where),
                line,
            ))

    for child in ast.walk(node):
        # A subscript is a call with the parentheses left off: a class
        # that defines __class_getitem__ runs that body for A[0], so
        # `X = A[0]` is a call at import time that no search for an
        # ast.Call can find.
        if isinstance(child, ast.Subscript) \
                and isinstance(child.value, ast.Name) \
                and child.value.id in class_names:
            violations.append(Violation(
                "no_top_level_code",
                "subscripts {} {}; A[...] calls the class's "
                "__class_getitem__, which is a body running at import with "
                "no call written anywhere".format(child.value.id, where),
                getattr(child, "lineno", line),
            ))

        # A walrus binds a name from inside an expression, so it rebinds
        # without ever being an assignment target: X = (int := print)
        # leaves int() meaning print(), and neither statement is an
        # assignment to int.
        if isinstance(child, ast.NamedExpr) and isinstance(child.target,
                                                           ast.Name):
            walrus = "in a := {}".format(where)
            at = getattr(child, "lineno", line)

            violations.extend(_rebind_violations(
                [child.target.id], walrus, at))
            violations.extend(_dunder_violations(
                [child.target.id], walrus, at))

    return violations


def _rebind_violations(names, where, line):
    """Rule 7 over the names a module-level statement binds.

    A name in PROTECTED_NAMES is one the other rules read as meaning what
    it says. Whatever else the module wants to call something, it may not
    call it that.
    """
    return [
        Violation(
            "no_top_level_code",
            "binds the name {} {}; the acceptance rules decide by that "
            "name, so a module may not give it another meaning".format(
                name, where),
            line,
        )
        for name in names if name in PROTECTED_NAMES
    ]


def _dunder_violations(names, where, line, allow=()):
    """Rule 7 over the dunder names a statement binds. See _is_dunder."""
    return [
        Violation(
            "no_top_level_code",
            "binds the dunder name {} {}; Python and pytest call dunders "
            "of their own accord, so binding one arranges for a body to run "
            "at import with no call written anywhere".format(name, where),
            line,
        )
        for name in names if _is_dunder(name) and name not in allow
    ]


def _importable():
    """The import allowlist, as one phrase a violation message can carry."""
    return ", ".join(
        name if bound == name else "{} as {}".format(name, bound)
        for name, bound in sorted(ALLOWED_IMPORTS.items())
    )


def _is_allowed_decorator(node):
    """True for @pytest.mark.NAME and @pytest.mark.NAME(...), nothing else."""
    if isinstance(node, ast.Call):
        node = node.func

    parts = []

    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value

    if not isinstance(node, ast.Name):
        return False

    parts.append(node.id)
    parts.reverse()

    return tuple(parts[:2]) == ALLOWED_DECORATOR and len(parts) > 2


def _assigned(targets):
    """Every name an assignment binds, unpacking a tuple or list target.

    "a, b = 1, 2" binds two names and is as much a constant as two separate
    lines. "obj.attr = ..." and "table[0] = ..." bind neither -- they reach
    into something that already exists, which is not what a module-level
    constant does.
    """
    out = []

    for target in targets:
        if isinstance(target, (ast.Tuple, ast.List)):
            out.extend(_assigned(target.elts))
        elif isinstance(target, ast.Starred):
            out.extend(_assigned([target.value]))
        else:
            out.append(target)

    return out


def _definition_violations(node, class_names=()):
    """Rule 7 over a def or a class: everything about it that runs at import.

    A def statement is not inert. Its decorators, its default arguments and
    its annotations are all evaluated when the statement is executed; only
    the body waits to be called. A class body is executed there and then,
    which is why it is walked with the same rule rather than trusted for
    being a ClassDef.
    """
    violations = []
    line = getattr(node, "lineno", 0)

    for decorator in node.decorator_list:
        if not _is_allowed_decorator(decorator):
            called = decorator.func if isinstance(decorator, ast.Call) \
                else decorator

            violations.append(Violation(
                "no_top_level_code",
                "decorates {} with {}; a decorator runs at import, so only "
                "pytest.mark.* is allowed".format(node.name, _spelled(called)),
                line,
            ))
            continue

        # pytest.mark.category(open("P", "w")) is an allowed decorator with
        # a call in its argument, which runs just the same.
        if isinstance(decorator, ast.Call):
            for argument in list(decorator.args) + [
                    keyword.value for keyword in decorator.keywords]:
                violations.extend(_value_violations(
                    argument, "in a decorator on {}".format(node.name), line,
                    class_names))

    if isinstance(node, ast.ClassDef):
        # "class Recorder(metaclass=print): pass" calls print while the
        # class statement is executed, and there is no ast.Call anywhere in
        # it for rule 7 to find: the class statement itself is the call.
        # **kwargs in a base list can carry a metaclass just as well, so a
        # keyword with no name is refused too. The templates emit
        # no class at all, so nothing here is refused in practice.
        for keyword in node.keywords:
            violations.append(Violation(
                "no_top_level_code",
                "creates class {} with {}=; a class keyword is called while "
                "the class statement runs, which is import time".format(
                    node.name, keyword.arg or "**"),
                line,
            ))

        for base in list(node.bases) + [
                keyword.value for keyword in node.keywords]:
            violations.extend(_value_violations(
                base, "as a base of class {}".format(node.name), line,
                class_names))

        return violations + _body_violations(
            node.body, inside_class=True, class_names=class_names)

    arguments = node.args

    for default in list(arguments.defaults) + [
            value for value in arguments.kw_defaults if value is not None]:
        violations.extend(_value_violations(
            default, "as a default argument of {}".format(node.name), line,
            class_names))

    annotated = list(arguments.posonlyargs) + list(arguments.args) \
        + list(arguments.kwonlyargs) \
        + [extra for extra in (arguments.vararg, arguments.kwarg)
           if extra is not None]

    annotations = [argument.annotation for argument in annotated
                   if argument.annotation is not None]

    if node.returns is not None:
        annotations.append(node.returns)

    for annotation in annotations:
        violations.extend(_value_violations(
            annotation, "in an annotation of {}".format(node.name), line,
            class_names))

    return violations


def _binding_violations(node, inside_class=False):
    """Rule 7 over what one statement imports and what it binds.

    Five statements bind a name where a generated module is allowed to have
    one -- an import, a def, a class and the two assignments -- and each of
    them can make an allowed spelling mean something else.

    What may be imported is an allowlist, not a list of refusals. Under a
    denylist an alias reaches one module under another module's name
    ("import subprocess as os"), a star import rebinds five names the rules
    read while naming none of them, and an import binds a dunder
    ("from os import makedirs as __getattr__") that Python then calls during
    collection. ALLOWED_IMPORTS says what a generated module may import and
    the one name each may be bound to, so a spelling nobody anticipated is
    refused for not being on the list rather than allowed for not being on a
    list of refusals.
    """
    violations = []
    line = getattr(node, "lineno", 0)
    where = "in a class body" if inside_class else "at module level"

    if isinstance(node, ast.Import):
        for alias in node.names:
            root = alias.name.split(".")[0]
            bound = alias.asname or root

            violations.extend(_dunder_violations([bound], where, line))

            if root not in ALLOWED_IMPORTS:
                violations.append(Violation(
                    "no_top_level_code",
                    "imports {}; a generated module may import only "
                    "{}".format(alias.name, _importable()),
                    line,
                ))
                continue

            if bound != ALLOWED_IMPORTS[root]:
                violations.append(Violation(
                    "no_top_level_code",
                    "imports {} as {}; it may be bound only to {}, because "
                    "the acceptance rules read a call by the name it is "
                    "written with".format(
                        alias.name, bound, ALLOWED_IMPORTS[root]),
                    line,
                ))

        return violations

    if isinstance(node, ast.ImportFrom):
        module = node.module or ""
        root = module.split(".")[0]
        spelled = "." * node.level + module or "."

        if node.level or root not in ALLOWED_IMPORTS:
            violations.append(Violation(
                "no_top_level_code",
                "imports from {}; a generated module may import only "
                "{}".format(spelled, _importable()),
                line,
            ))

        for alias in node.names:
            if alias.name == "*":
                violations.append(Violation(
                    "no_top_level_code",
                    "imports * from {}; a star import binds whatever the "
                    "other module exports, named nowhere in this one -- "
                    "numpy alone rebinds max, min, abs, round and bool, "
                    "which the acceptance rules read".format(spelled),
                    line,
                ))
                continue

            if alias.asname:
                violations.append(Violation(
                    "no_top_level_code",
                    "imports {} from {} as {}; what is imported is not the "
                    "module's to rename, because the rules read a call by "
                    "the name it is written with".format(
                        alias.name, spelled, alias.asname),
                    line,
                ))

            bound = alias.asname or alias.name

            violations.extend(_dunder_violations([bound], where, line))
            violations.extend(_rebind_violations(
                [bound], "by importing it from {}".format(spelled), line))

            if root in GUARDED_MODULES \
                    and alias.name not in GUARDED_MODULES[root]:
                violations.append(Violation(
                    "no_top_level_code",
                    "imports {} from {}; a generated module may use only "
                    "{} from it".format(
                        alias.name, root, ", ".join(GUARDED_MODULES[root])),
                    line,
                ))

        return violations

    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                         ast.ClassDef)):
        violations.extend(_dunder_violations(
            [node.name], where, line,
            allow=("__init__",) if inside_class else ()))

        return violations + _rebind_violations(
            [node.name], "by defining it", line)

    if isinstance(node, (ast.Assign, ast.AnnAssign)):
        targets = node.targets if isinstance(node, ast.Assign) \
            else [node.target]

        names = [target.id for target in _assigned(targets)
                 if isinstance(target, ast.Name)]

        violations.extend(_dunder_violations(names, where, line))
        violations.extend(_rebind_violations(names, "by assignment", line))

    return violations


# What a class body may contain. A class body is not a module body: it has
# no imports to make and nothing to define but methods and constants.
CLASS_BODY_STATEMENTS = (
    ast.FunctionDef,
    ast.AsyncFunctionDef,
    ast.Assign,
    ast.AnnAssign,
)


def _body_violations(body, inside_class=False, class_names=()):
    """Rule 7 over one suite of statements that runs at import time.

    Called for the module's own body and, through _definition_violations,
    for the body of every class defined in it: a class body runs at import
    exactly like the statements around it, so `class X: open("P", "w")`
    opens the file while pytest collects the module.

    Both scopes are checked the same way, binding checks included. A class
    body binds in the class namespace, but it also RESOLVES names there
    first, so `str = open` followed by `x = str("MARK", "w")` calls open
    during collection, and `def str(x): ...` and `str: int = open` do the
    same. Exempting a class body would also let it define __init_subclass__
    or __class_getitem__, each of which Python calls with no call appearing
    anywhere in the file.
    """
    violations = []
    where = "in a class body" if inside_class else "at module level"
    allowed = CLASS_BODY_STATEMENTS if inside_class else TOP_LEVEL_STATEMENTS
    shapes = "define methods and assign constants" if inside_class \
        else "import, define and assign constants"

    for node in body:
        if _is_docstring(node):
            continue

        # A class body of nothing but `pass` runs nothing, so it passes.
        if inside_class and isinstance(node, ast.Pass):
            continue

        if not isinstance(node, allowed):
            violations.append(Violation(
                "no_top_level_code",
                "{} runs at import; a generated module may only {} "
                "{}".format(type(node).__name__, shapes, where),
                getattr(node, "lineno", 0),
            ))
            continue

        violations.extend(_binding_violations(node, inside_class))

        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)):
            violations.extend(_definition_violations(node, class_names))
            continue

        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue

        targets = node.targets if isinstance(node, ast.Assign) \
            else [node.target]

        for target in _assigned(targets):
            if not isinstance(target, ast.Name):
                violations.append(Violation(
                    "no_top_level_code",
                    "assigns to a {} {}; only a plain name may be "
                    "assigned".format(type(target).__name__, where),
                    node.lineno,
                ))

        # Python evaluates a module-level or class-level annotation eagerly,
        # so `X: open("P", "w") = 1` opens the file, so annotations are
        # checked too.
        if isinstance(node, ast.AnnAssign):
            violations.extend(_value_violations(
                node.annotation, "in an annotation {}".format(where),
                node.lineno, class_names))

        if node.value is None:
            continue

        violations.extend(_value_violations(
            node.value, "to build a value {}".format(where), node.lineno,
            class_names))

    return violations


def _top_level_violations(tree):
    """Rule 7: nothing runs at import except imports, defs and constants."""
    class_names = frozenset(
        node.name for node in tree.body if isinstance(node, ast.ClassDef))

    return _body_violations(tree.body, class_names=class_names)


def _guarded_violations(value, attribute, line, how):
    """Rule 5 over one attribute of a module that reaches the system.

    `value` is whatever the attribute was taken from, so this fires only
    when that is the plain module name the import allowlist binds -- os,
    asyncio, time or wave. Anything those modules offer beyond
    GUARDED_MODULES is a way out of the harness that no generator writes.
    """
    if not (isinstance(value, ast.Name) and value.id in GUARDED_MODULES):
        return []

    if attribute in GUARDED_MODULES[value.id]:
        return []

    return [Violation(
        "no_escape",
        "{} {}.{}; a generated test may use only {} from {}, so anything "
        "else in it is a way out of the harness".format(
            how, value.id, attribute, ", ".join(GUARDED_MODULES[value.id]),
            value.id),
        line,
    )]


def _chain(node):
    """An attribute chain rooted at a plain name, as a list of segments.

    ["np", "lib", "format", "os"] for np.lib.format.os, and None when the
    chain does not start at a name -- f().x, d["k"].x -- because there is
    no module name to read at the root of those.
    """
    parts = []

    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value

    if not isinstance(node, ast.Name):
        return None

    parts.append(node.id)
    parts.reverse()

    return parts


def _outermost_attributes(tree):
    """Every attribute chain in the tree, each reported once, at its end.

    np.lib.format.os is four Attribute nodes nested inside each other, and
    walking them all would report the same chain four times. Only the
    outermost one -- the node that is not another attribute's value -- has
    the whole chain under it.
    """
    inner = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) \
                and isinstance(node.value, ast.Attribute):
            inner.add(id(node.value))

    return [node for node in ast.walk(tree)
            if isinstance(node, ast.Attribute) and id(node) not in inner]


def _chain_violations(node):
    """Rule 5 over one attribute chain, read whole rather than two deep.

    Two questions. Does the chain NAME a module that reaches the system,
    at any depth? np.sys.modules["subprocess"], np.ctypeslib.ctypes and
    regression.dashboard.subprocess each reach a forbidden module through a
    name the import allowlist permits, and each of them is a chain whose
    second or third segment is the module itself.

    And, for the two packages a generated module imports whole, is the
    first attribute one the templates reach for? A package carries every
    module it imports as an attribute, so np and regression are doors to
    the whole interpreter unless what may be taken from them is a list.
    """
    parts = _chain(node)

    if parts is None:
        return []

    line = getattr(node, "lineno", 0)
    violations = []

    for depth, part in enumerate(parts):
        if part not in SYSTEM_MODULES:
            continue

        # os at the root of the chain is the import allowlist's os, and
        # GUARDED_MODULES decides what may be taken from it.
        if depth == 0 and part in GUARDED_MODULES:
            continue

        violations.append(Violation(
            "no_escape",
            "names {} in {}; {} reaches the system, and a package carries "
            "every module it imports as an attribute, so naming one part "
            "way along a chain reaches it just as an import would".format(
                part, ".".join(parts), part),
            line,
        ))

    surface, longest = PACKAGE_SURFACE.get(parts[0], (None, 0))

    if surface is None:
        return violations

    if parts[1] not in surface:
        violations.append(Violation(
            "no_escape",
            "names {} in {}; a generated test may take only {} from {}, "
            "because {} carries every module it imports as an "
            "attribute".format(parts[1], ".".join(parts),
                               ", ".join(surface), parts[0], parts[0]),
            line,
        ))
    elif len(parts) > longest:
        violations.append(Violation(
            "no_escape",
            "reaches {} deep into {}; the templates write no chain longer "
            "than {} segments, and what sits further along one cannot be "
            "read from here".format(".".join(parts), parts[0], longest),
            line,
        ))

    return violations


def _attribute_name_violations(node):
    """Rule 5 over one attribute name, whatever it is taken from.

    _chain_violations needs a name at the root of the chain, and
    asyncio.get_event_loop().subprocess_exec(...) has a call there instead:
    the loop object is handed over by a call the rules allow, and the
    process API hangs off it. So the name itself is read, split on "_",
    for a system module hiding inside it.
    """
    for token in node.attr.split("_"):
        if token in SYSTEM_MODULES:
            return [Violation(
                "no_escape",
                "names .{}; a name built out of {} reaches that module, "
                "whichever object it is taken from".format(node.attr, token),
                getattr(node, "lineno", 0),
            )]

    return []


def _reading_violations(tree):
    """Rule 5 over every dunder and every namespace name the module reads.

    Rule 7 refuses BINDING a dunder, which covers the hooks Python calls by
    itself. Reading one is the other half: __builtins__["__import__"] is
    every forbidden call at once, __class__ and __globals__ walk from any
    object to the interpreter, and none of it is a call this module can
    match by name. So a generated module names no dunder at all, and no
    namespace builtin either.
    """
    violations = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            name, kind = node.id, "the name"
        elif isinstance(node, ast.Attribute):
            name, kind = node.attr, "the attribute"
        else:
            continue

        line = getattr(node, "lineno", 0)

        if _is_dunder(name):
            violations.append(Violation(
                "no_escape",
                "reads {} {}; a dunder is the interpreter's own machinery, "
                "and reaching it names every forbidden call at once without "
                "writing one".format(kind, name),
                line,
            ))
        elif name in NAMESPACE_NAMES:
            violations.append(Violation(
                "no_escape",
                "reads {} {}; it hands back a namespace, and a name looked "
                "up in one is written nowhere these rules can read "
                "it".format(kind, name),
                line,
            ))

    return violations


def _subscript_call_violations(tree):
    """Rule 5 over a call whose callee is a subscript.

    d["k"]() writes no callee name anywhere, so every rule that matches one
    is blind to it: __builtins__["__import__"]("subprocess") and
    ctypes.CDLL(None)["system"](b"id") are both a lookup followed by a call.
    The templates call functions and methods by name and nothing else.
    """
    return [Violation(
        "no_escape",
        "calls the result of a subscript; which function that is would "
        "only be settled while the test ran, so no rule here can read it",
        getattr(node, "lineno", 0),
    ) for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Subscript)]


def _is_infinite_loop(node):
    """while True: with no break anywhere inside it."""
    if not isinstance(node, ast.While):
        return False

    test = node.test

    if not (isinstance(test, ast.Constant) and test.value is True):
        return False

    for child in ast.walk(node):
        if isinstance(child, ast.Break):
            return False

    return True


def check_source(source, filename="<generated>"):
    """Run every acceptance rule over a module's source.

    Returns a list of Violation. An empty list means the module is fit to
    run -- not that it is a good test, which no mechanical check can decide.
    """
    violations = []

    try:
        tree = ast.parse(source, filename=filename)
    except SyntaxError as exc:
        return [Violation("parses", "module is not valid Python: {}".format(exc),
                          exc.lineno or 0)]

    tests = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name.startswith("test_")
    ]

    # 1. There has to be something to run.
    if not tests:
        violations.append(Violation(
            "has_tests", "module defines no test functions"
        ))

    # 2. Every test must be able to fail.
    for node in tests:
        if not _asserts_or_skips(node):
            violations.append(Violation(
                "must_assert",
                "{} cannot fail: no assert, skip or raises".format(node.name),
                node.lineno,
            ))

    # 3. Names must be unique, or pytest silently runs only the last one.
    seen = set()

    for node in tests:
        if node.name in seen:
            violations.append(Violation(
                "unique_names",
                "{} is defined more than once; earlier copies never "
                "run".format(node.name),
                node.lineno,
            ))

        seen.add(node.name)

    # 4. No network.
    for node in ast.walk(tree):
        names = []

        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            names = [node.module]

        for name in names:
            root = name.split(".")[0]

            if root in FORBIDDEN_IMPORTS:
                violations.append(Violation(
                    "no_network",
                    "imports {}; a regression test must not depend on "
                    "anything outside the harness".format(name),
                    node.lineno,
                ))

    # 5. No shelling out, no evaluating strings, and no reaching the system
    #    under another name.
    #
    # The import first, because the call is matched on the name the module
    # chose for it. "from os import system as int" puts a shell behind a
    # spelling this rule allows and rule 7 permits, with the name "system"
    # appearing nowhere a call could be read from. What is imported is not
    # the module's to rename.
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue

        for alias in node.names:
            if alias.name in FORBIDDEN_CALLS:
                violations.append(Violation(
                    "no_escape",
                    "imports {} from {}; generated tests run unattended and "
                    "must not execute arbitrary text, whatever the import "
                    "calls it".format(alias.name, node.module or "."),
                    node.lineno,
                ))

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue

        name = None

        if isinstance(node.func, ast.Attribute):
            name = node.func.attr
        elif isinstance(node.func, ast.Name):
            name = node.func.id

        if name in FORBIDDEN_CALLS:
            violations.append(Violation(
                "no_escape",
                "calls {}(); generated tests run unattended and must not "
                "execute arbitrary text".format(name),
                node.lineno,
            ))

        # And anything reached THROUGH one of the forbidden modules, whether
        # or not an import statement gave it away. Rule 4 sees the import;
        # this sees subprocess.run(...) in a module that never imported it,
        # which is a NameError rather than a breach -- but it is the shape
        # an escape takes, and reporting it is cheaper than deciding which
        # names are dangerous one at a time.
        if isinstance(node.func, ast.Attribute) \
                and isinstance(node.func.value, ast.Name) \
                and node.func.value.id in FORBIDDEN_IMPORTS:
            violations.append(Violation(
                "no_escape",
                "calls {}.{}(); generated tests must not reach outside the "
                "harness".format(node.func.value.id, name),
                node.lineno,
            ))

        # getattr() is the same call with the name moved out of reach: this
        # rule matches how a callee is WRITTEN, and
        # getattr(os, "sys" + "tem")("...") writes neither "os.system" nor
        # "system" anywhere. A literal name is read as if it had been
        # written with a dot; a name the module builds cannot be read at
        # all, so it is refused rather than guessed at.
        if not (isinstance(node.func, ast.Name)
                and node.func.id == "getattr"):
            continue

        attribute = node.args[1] if len(node.args) > 1 else None

        if not (isinstance(attribute, ast.Constant)
                and isinstance(attribute.value, str)):
            violations.append(Violation(
                "no_escape",
                "calls getattr() with an attribute name that is not a "
                "literal; which attribute that is would only be settled "
                "while the test ran, so no rule here can read it",
                node.lineno,
            ))
            continue

        if attribute.value in FORBIDDEN_CALLS:
            violations.append(Violation(
                "no_escape",
                "reaches {} through getattr(); generated tests run "
                "unattended and must not execute arbitrary text, however "
                "the name is spelled".format(attribute.value),
                node.lineno,
            ))

        violations.extend(_guarded_violations(
            node.args[0] if node.args else None, attribute.value,
            node.lineno, "reaches"))

    # And every other way an attribute of one of those modules is named. A
    # list of escapes covers only the ones already on it: past os.system and
    # os.popen sit os.posix_spawn, os.spawnv and os.startfile, and naming
    # those three leaves the fourth. GUARDED_MODULES says instead what a
    # generated test may use from os, asyncio, time and wave; everything
    # else in them is refused without having to be named.
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            violations.extend(_guarded_violations(
                node.value, node.attr, node.lineno, "names"))
            violations.extend(_attribute_name_violations(node))

    # And the chains those two checks cannot see. Both read one attribute
    # and the plain name directly under it, so they stop at the second
    # segment: np.sys.modules, np.ctypeslib.ctypes.CDLL and
    # regression.dashboard.subprocess.run all take an attribute of an
    # attribute, where the module that reaches the system sits further
    # along than either check looks.
    for node in _outermost_attributes(tree):
        violations.extend(_chain_violations(node))

    # And the two shapes that carry a name no rule can match: a dunder,
    # which is the interpreter's own machinery, and a call through a
    # subscript, whose callee is settled only while the test runs.
    violations.extend(_reading_violations(tree))
    violations.extend(_subscript_call_violations(tree))

    # 6. No test that can never finish.
    for node in ast.walk(tree):
        if _is_infinite_loop(node):
            violations.append(Violation(
                "must_terminate",
                "while True with no break; an unattended run would hang here",
                node.lineno,
            ))

    # 7. What runs at import is an allowlist: the docstring, the imports on
    #    ALLOWED_IMPORTS, defs and classes with ordinary names, and
    #    constants. No dunder is bound anywhere, and no class the module
    #    defines is called or subscripted at module level.
    violations.extend(_top_level_violations(tree))

    return violations


def check_file(path=None):
    """Acceptance rules against a file on disk."""
    path = path or GENERATED_TEST_PATH

    if not os.path.exists(path):
        return [Violation("exists", "no generated module at {}".format(path))]

    with open(path, encoding="utf-8") as handle:
        return check_source(handle.read(), filename=path)


# ---------------------------------------------------------------------------
# Approval
# ---------------------------------------------------------------------------

# Lines carrying this run's measurements rather than this run's test code.
# They are the device readings of the moment -- retry_total counts
# reconnects since the board booted, battery moves with the supply -- so
# they differ on every run even when nothing about the tests has changed.
RUN_SPECIFIC = ("METRICS = ",)

EXCLUDED_NOTE = "<run measurements, excluded from the fingerprint>"


def _is_measurements(line):
    """True when this line is a RUN_SPECIFIC assignment of plain data.

    The prefix alone cannot decide it. Blanking every line that begins
    "METRICS = " would hide whatever follows the equals sign from the
    fingerprint -- `METRICS = __import__("shutil").rmtree(...) or {}`
    included -- and leave a previously granted approval matching. A reading
    is a literal; anything a literal cannot express is code, and code is
    hashed like the rest of the module.
    """
    if not any(line.startswith(prefix) for prefix in RUN_SPECIFIC):
        return False

    try:
        ast.literal_eval(line.split("=", 1)[1].strip())
    except (SyntaxError, ValueError, MemoryError, RecursionError):
        return False

    return True


def approvable_source(text):
    """The module's text with its run measurements blanked.

    Approval is of test code, not of the readings a run happened to take.
    Hashing the file whole would make every run produce a module no approval
    could match: the next run embeds different metrics, enforce() finds no
    approval, and HA_REQUIRE_APPROVAL=1 could never pass on real hardware --
    not on the bench and not in CI.

    Everything that decides what the tests DO is still hashed: the
    thresholds, the limits, the scenarios, every assertion. Only the
    measured values are dropped, and the run prints those anyway.
    """
    out = []

    for line in text.splitlines(True):
        if _is_measurements(line):
            name = line.split("=", 1)[0].rstrip()
            out.append("{} = {}\n".format(name, EXCLUDED_NOTE))
            continue

        out.append(line)

    return "".join(out)


def fingerprint(path=None):
    """SHA-256 of the module's test code, so approval is tied to content.

    The run's own measurements are excluded -- see approvable_source.
    """
    path = path or GENERATED_TEST_PATH

    with open(path, encoding="utf-8", errors="replace") as handle:
        text = approvable_source(handle.read())

    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _record_path():
    return in_base(RECORD_PATH)


def load_records():
    path = _record_path()

    if not os.path.exists(path):
        return []

    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return []

    return data if isinstance(data, list) else []


def save_records(records):
    path = _record_path()

    with open(path, "w", encoding="utf-8") as handle:
        json.dump(records, handle, indent=2, sort_keys=True)

    return path


def approve(path=None, approved_by="", note=""):
    """Record a human decision about the module as it stands right now."""
    path = path or GENERATED_TEST_PATH

    if not approved_by:
        raise ValueError(
            "approved_by is required -- an approval nobody signed is not an "
            "approval"
        )

    violations = check_file(path)

    if violations:
        raise ValueError(
            "cannot approve a module that breaks acceptance rules:\n  "
            + "\n  ".join(str(v) for v in violations)
        )

    record = {
        "fingerprint": fingerprint(path),
        "state": APPROVED,
        "approved_by": approved_by,
        "approved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "note": note,
        "module": os.path.basename(path),
    }

    records = load_records()
    records.append(record)
    save_records(records)

    return record


def approval_for(path=None):
    """The approval matching the module's current bytes, or None.

    Editing an approved module changes its fingerprint, so the approval stops
    matching. That is the intended behaviour: approval is of content, not of
    a filename.
    """
    path = path or GENERATED_TEST_PATH

    if not os.path.exists(path):
        return None

    current = fingerprint(path)

    for record in reversed(load_records()):
        if record.get("fingerprint") == current:
            return record

    return None


def approval_required():
    return os.getenv(REQUIRE_APPROVAL_ENV) == "1"


class ApprovalRequired(RuntimeError):
    """HA_REQUIRE_APPROVAL=1 and this module has no approval.

    `reason` is the fact and `advice` the command that settles it; str() is
    both, which is what an operator reading a stopped run needs. A caller
    that knows the advice cannot apply prints the reason alone -- the
    orchestrator's escalation is one, because it restores the module that
    ran, so the bytes the advice offers to approve are gone by the time an
    operator reads it.
    """

    def __init__(self, reason, advice=""):
        super().__init__(reason + ("\n" + advice if advice else ""))

        self.reason = reason
        self.advice = advice


def _quoted(path):
    """`path` as one argument of a command line, for this platform's shell.

    An unquoted path with a space in it is two arguments, and the operator
    is told to type a command that fails. It is not a rare shape: the
    writable fallback lives under LOCALAPPDATA, which is
    C:/Users/<name>/AppData/Local, and a Windows account name may be
    "Jane Doe".

    list2cmdline is the Windows rule (CommandLineToArgvW's, which is what
    the C runtime and PowerShell's native-command path undo), and
    shlex.quote is the POSIX one. Neither adds quotes to a path that does
    not need them, so the common case still prints bare.
    """
    if os.name == "nt":
        import subprocess

        return subprocess.list2cmdline([path])

    import shlex

    return shlex.quote(path)


def approve_command(path):
    """The command that approves `path`, exactly as it has to be typed.

    Verified by tests/test_governance.py against main(), because a printed
    command argparse will not accept is worse than no advice at all: this is
    the one command the program asks an operator to type, so --path has to
    be accepted where the sentence puts it. The path is quoted for the same
    reason -- see _quoted.
    """
    return ('python -m regression.governance --path {} approve '
            '--by "your name"'.format(_quoted(path)))


def enforce(path=None):
    """Gate a run on the governance rules. Returns the module's state.

    Raises ApprovalRequired (a RuntimeError) when approval is required and
    absent -- the only condition that stops a run. Everything else is
    either a rule violation worth reporting or a bench run nobody will
    quote, and a rule violation under HA_REQUIRE_APPROVAL=1 raises a plain
    RuntimeError instead, because no approval can settle it.
    """
    path = path or GENERATED_TEST_PATH

    violations = check_file(path)

    if violations:
        print("\nGovernance: {} acceptance rule violation(s)".format(
            len(violations)))

        for violation in violations:
            print("  -", violation)

        if approval_required():
            raise RuntimeError(
                "{}=1 and {} breaks {} acceptance rule(s); refusing to run. "
                "Fix the generator: an approval cannot help here, because "
                "approve() refuses a module that breaks a rule.".format(
                    REQUIRE_APPROVAL_ENV, os.path.basename(path),
                    len(violations))
            )

        print("  Running anyway: approval is not required for this run.")

        return DRAFT

    record = approval_for(path)

    if record:
        print("\nGovernance: approved by {} on {}".format(
            record.get("approved_by", "?"), record.get("approved_at", "?")))

        return APPROVED

    if approval_required():
        # Named with --path, because a run governs two modules and the
        # advice has to be runnable as printed -- in the order argparse
        # accepts. The caller decides whether it applies at all: an
        # escalated module is replaced by the next run, so approving it
        # would be advice in a circle, and the orchestrator prints the
        # reason without it.
        raise ApprovalRequired(
            "{}=1 but {} has no approval.".format(
                REQUIRE_APPROVAL_ENV, os.path.basename(path)),
            "Review it and run:\n    {}".format(approve_command(path)),
        )

    print("\nGovernance: acceptance rules pass; not approved (approval not "
          "required for this run)")

    return REVIEWED


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def generated_modules():
    """Every generated module present, in the order a run executes them.

    "Present" and "executed this run" are the same list only when the
    pipeline has just run. A stale test_from_canonical.py -- left behind
    when Requirements is Cleared, for instance -- would otherwise be
    validated, approved and reported although no run touches it, so the
    orchestrator deletes it rather than leaving it to be picked up.

    A run writes two: the planner-selected module and the requirement
    suite, and governs both. A CLI acting on the first alone would leave the
    requirement suite unapproved, so a run under HA_REQUIRE_APPROVAL=1 would
    refuse to start and name a module the operator had just approved.
    """
    return [path for path in (GENERATED_TEST_PATH, CANONICAL_TEST_PATH)
            if os.path.exists(path)]


def _targets(args):
    """The modules a command acts on: --path narrows it to one.

    A --path that does not exist is a typo, not a crash, so the commands
    print a sentence rather than ending in a FileNotFoundError traceback.
    """
    if args.path:
        if not os.path.exists(args.path):
            print("No module at", args.path)
            return []

        return [args.path]

    return generated_modules()


def _cmd_validate(args):
    targets = _targets(args)

    if not targets:
        print("No generated module yet. Run the pipeline once.")
        return 1

    failed = 0

    for path in targets:
        violations = check_file(path)

        if not violations:
            print("Acceptance rules pass:", path)
            continue

        failed += 1

        print("{}: {} violation(s):".format(path, len(violations)))

        for violation in violations:
            print("  -", violation)

    return 1 if failed else 0


def _cmd_approve(args):
    targets = _targets(args)

    if not targets:
        print("No generated module yet. Run the pipeline once.")
        return 1

    for path in targets:
        record = approve(path, approved_by=args.by, note=args.note)

        print("Approved {} as {}".format(
            record["module"], record["fingerprint"][:12]))
        print("  by {} at {}".format(
            record["approved_by"], record["approved_at"]))

    return 0


def _cmd_status(args):
    targets = _targets(args)

    if not targets:
        print("No generated module yet. Run the pipeline once.")
        return 1

    for path in targets:
        _print_status(path)

    return 0


def _print_status(path):
    violations = check_file(path)
    record = approval_for(path)

    print("Module:      ", path)
    print("Fingerprint: ", fingerprint(path)[:12])
    print("Rules:       ", "pass" if not violations else
          "{} violation(s)".format(len(violations)))

    if record:
        print("State:        approved by {} at {}".format(
            record["approved_by"], record["approved_at"]))
    else:
        print("State:        not approved")

    print("Enforcement: ", "required ({}=1)".format(REQUIRE_APPROVAL_ENV)
          if approval_required() else "advisory")

    for violation in violations:
        print("  -", violation)

    print()


def main(argv=None):
    import argparse

    parser = argparse.ArgumentParser(
        description="Acceptance rules and approval for generated tests"
    )
    parser.add_argument("--path", default=None, help="module to act on")

    sub = parser.add_subparsers(dest="command")

    def add(name, help_text):
        """A subcommand that also takes --path, wherever the operator put it.

        Into its own destination, because a subparser's default overwrites
        the value the top-level option already parsed: --path before the
        subcommand would be thrown away by the subcommand's own default.
        """
        command = sub.add_parser(name, help=help_text)
        command.add_argument("--path", dest="sub_path", default=None,
                             help="module to act on")

        return command

    add("validate", "run the acceptance rules")
    add("status", "rules plus approval state")

    approve_parser = add("approve", "record an approval")
    approve_parser.add_argument("--by", required=True, help="who approved it")
    approve_parser.add_argument("--note", default="", help="why")

    args = parser.parse_args(argv)

    # Either side of the subcommand, so the advice printed on a refusal is
    # runnable whichever way it was written.
    args.path = getattr(args, "sub_path", None) or args.path

    handlers = {
        "validate": _cmd_validate,
        "approve": _cmd_approve,
        "status": _cmd_status,
    }

    handler = handlers.get(args.command or "status")

    return handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
