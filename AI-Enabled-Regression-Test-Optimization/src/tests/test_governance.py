# Copyright (c) 2026 Prajnaana Technologies Pvt. Ltd.
# Original author: Dhanya Shree S
# SPDX-License-Identifier: MIT
#
# Licensed under the MIT License. See LICENSE in the project root.

"""Tests for the acceptance rules and the approval record.

These guard the claim that a generated test is checked before it counts.
A validator that passes everything is indistinguishable from no validator, so
most of these are about what it must reject.
"""

import textwrap

import pytest

from regression import governance


def rules_broken(source):
    return {v.rule for v in governance.check_source(source)}


# The module-level shape both generators emit, as one string constant so the
# nested triple quotes in the generated docstring stay readable.
SHAPE_THE_TEMPLATES_EMIT = '''\
"""A generated module."""

import os

import pytest

from regression import measurements

SETTLE = 2.0
GAIN_MIN, GAIN_MAX = 0.45, 0.95
USE_SAMPLES = int(float(os.getenv("HA_TEST_SECONDS", "0.125")) * 16000)
PACKETS = 80
STRESS_STREAMS = max(3, PACKETS // 8)
TEST_SIGNAL = r"C:\\work\\test_signal.wav"
METRICS = {'memory': 0.37, 'retry': 0, 'unmeasured': ['power']}
THRESHOLDS = {'power': {'min': 0, 'max': 60}}
INTENSITY = "high"


def helper():
    return measurements, pytest


class Recorder(object):
    pass


@pytest.mark.category("audio")
def test_x():
    assert SETTLE > 0
'''


# --------------------------------------------------------------------------
# What must be rejected
# --------------------------------------------------------------------------

def test_a_test_that_cannot_fail_is_rejected():
    """The whole reason this module exists."""
    source = "def test_nothing():\n    x = 1 + 1\n"

    assert "must_assert" in rules_broken(source)


def test_a_module_with_no_tests_is_rejected():
    assert "has_tests" in rules_broken("VALUE = 3\n")


def test_duplicate_names_are_rejected():
    """pytest silently runs only the last definition."""
    source = (
        "def test_same():\n    assert True\n\n"
        "def test_same():\n    assert True\n"
    )

    assert "unique_names" in rules_broken(source)


def test_network_access_is_rejected():
    source = "import requests\n\ndef test_x():\n    assert True\n"

    assert "no_network" in rules_broken(source)


def test_socket_import_is_rejected():
    source = "import socket\n\ndef test_x():\n    assert True\n"

    assert "no_network" in rules_broken(source)


def test_shelling_out_is_rejected():
    source = "import os\n\ndef test_x():\n    os.system('ls')\n    assert True\n"

    assert "no_escape" in rules_broken(source)


def test_eval_is_rejected():
    source = "def test_x():\n    eval('1+1')\n    assert True\n"

    assert "no_escape" in rules_broken(source)


@pytest.mark.parametrize("statement", [
    # The shape the header injection takes: a field ends the "#" comment it
    # is pasted into and what follows is code.
    "PWNED = print('owned')",
    "open('owned.txt', 'w').write('x')",
    "if True:\n    PWNED = 1",
    "for _ in range(3):\n    PWNED = 1",
    "try:\n    PWNED = 1\nexcept Exception:\n    pass",
    "import sys\nsys.path[0] = '.'",
    "del pytest",
])
def test_code_at_module_level_is_rejected(statement):
    """A generated module may import, define and assign. Nothing else.

    generate_from_canonical builds its header by formatting the source
    document's name and the firmware build id into "#" comment lines. A
    newline in either field ends the comment and starts a statement, and the
    module then runs it the moment pytest imports the file -- before any rule
    about the tests themselves has been consulted. This rule reads what
    sits between the functions, so such a module is refused.
    """
    source = "{}\n\ndef test_x():\n    assert True\n".format(statement)

    assert "no_top_level_code" in rules_broken(source)


def test_the_module_level_shapes_the_templates_emit_are_accepted():
    """The two real generators must still come out clean.

    Both are measured against this rule: the planner's module and the
    requirement suite each report zero violations. What follows is the
    shape they have -- a docstring, imports, constants
    (METRICS among them, and the intensity arithmetic that reads an
    environment variable), the helpers and the tests.
    """
    assert governance.check_source(SHAPE_THE_TEMPLATES_EMIT) == []


def test_the_real_generated_header_carries_no_statement():
    """The header as generate_from_canonical actually formats it."""
    from regression.ai_engine import generate_from_canonical

    tail = "\n\ndef test_x():\n    assert True\n"

    clean = generate_from_canonical.HEADER.format(
        source="canonical.json", build_id="v1.0+9e4947d1",
        total=2, runnable=1, skipped=1) + tail

    assert governance.check_source(clean) == []

    poisoned = generate_from_canonical.HEADER.format(
        source="canonical.json\nPWNED_SCHEMA = print('owned')",
        build_id="v1.0+9e4947d1", total=2, runnable=1, skipped=1) + tail

    assert "no_top_level_code" in rules_broken(poisoned)


# Every statement below performs its side effect when the module is
# imported, and none of it sits in the VALUE of an Assign or an AnnAssign --
# so rule 7 has to look further than assigned values to catch them. Each
# entry is (label, statement).
IMPORT_TIME_SIDE_EFFECTS = [
    # A class body runs where it stands, so the rule has to look inside a
    # ClassDef rather than allow it whole.
    ("a class body", "class X:\n    open('owned', 'w')"),

    # A decorator is a call that runs at collection time.
    ("a decorator",
     "import pathlib\n@(lambda f: pathlib.Path('owned').touch() or f)\n"
     "def helper():\n    pass"),
    ("a named decorator", "@print\ndef helper():\n    pass"),

    # Default arguments are evaluated by the def statement, not by the call.
    ("a default argument",
     "import pathlib\ndef helper(x=pathlib.Path('owned').touch()):\n    pass"),

    # This one forges a verdict line on stdout while pytest collects.
    ("a default argument that prints",
     "def helper(x=print('Result: pass')):\n    pass"),
    ("a keyword-only default",
     "def helper(*, x=print('Result: pass')):\n    pass"),

    # Annotations too: Python before 3.14 evaluates them eagerly here.
    ("an argument annotation", "def helper(x: open('owned', 'w')):\n    pass"),
    ("a return annotation", "def helper() -> open('owned', 'w'):\n    pass"),
    ("an annotated assignment", "X: open('owned', 'w') = 1"),

    # A base class is an expression like any other.
    ("a base class", "class X(open('owned', 'w')):\n    pass"),

    # An allowed decorator with a call inside its argument.
    ("a mark argument",
     "import pytest\n@pytest.mark.category(open('owned', 'w'))\n"
     "def test_y():\n    assert True"),
]


@pytest.mark.parametrize("statement", [
    pytest.param(statement, id=label)
    for label, statement in IMPORT_TIME_SIDE_EFFECTS
])
def test_anything_that_runs_at_import_is_rejected(statement):
    """Rule 7 covers every expression a module-level statement evaluates.

    "Nothing runs at import except imports, defs and constants" is a
    documented guarantee, and each statement here breaks it somewhere
    other than the value of an assignment: a class body, a decorator, a
    default argument, an annotation and a base class each evaluate an
    expression of their own, so the rule reads all of them.
    """
    source = "{}\n\ndef test_x():\n    assert True\n".format(statement)

    assert "no_top_level_code" in rules_broken(source), source


# None of the statements below calls anything the rules forbid. Each one
# changes what an allowed name means first, and every rule that follows
# matches a name. Each entry is (label, statement).
REBINDINGS = [
    # A shell behind an allowed spelling. This also reaches past rule 5,
    # which matches the local name: "system" appears nowhere in the module.
    ("an import alias over an allowed call",
     'from os import system as int\nLIMIT = int("touch SHELL_TOUCHED")'),

    # A forged verdict line on stdout, printed while pytest collects the
    # file. str() is an allowed top-level call, so the second line reads as
    # one.
    ("a def over an allowed call",
     'def str(x):\n    print("Result: pass")\n    return x\n\nNAME = str("a")'),

    # The same, with no def and no import.
    ("an assignment over an allowed call",
     'int = print\nX = int("Result: pass")'),

    # A walrus binds from inside an expression, so it is not a target.
    ("a walrus over an allowed call",
     'X = (int := print)\nY = int("Result: pass")'),

    # Rule 7 reads os.getenv as the environment; this makes os whatever the
    # module wants, and rule 4 is the only thing that sees the import.
    ("an import alias over the os module", "import time as os"),

    # A class statement calls its metaclass, and there is no ast.Call in it.
    ("a metaclass", "class Recorder(metaclass=print):\n    pass"),

    # Python calls this for any name the module does not define, and pytest
    # looks names up on a module it has just collected.
    ("a module __getattr__",
     'def __getattr__(name):\n    print("Result: pass")\n    return 1'),
    ("an assigned module __getattr__", "__getattr__ = print"),
]


@pytest.mark.parametrize("statement", [
    pytest.param(statement, id=label) for label, statement in REBINDINGS
])
def test_rebinding_a_name_the_rules_decide_by_is_rejected(statement):
    """The allowlist matches names, and the module says what a name means.

    So the allowed spellings are allowed only while they still mean what
    they say. Each statement here rebinds one of them -- the first to a
    shell -- and every rule that follows reads a call by how it is written,
    not by what the module has bound that spelling to.
    """
    source = "{}\n\ndef test_x():\n    assert True\n".format(statement)

    assert "no_top_level_code" in rules_broken(source), source


def test_an_import_of_a_forbidden_call_is_rejected_under_any_name():
    """Rule 5 matches the call, and the module chooses what to call it."""
    for statement in ("from os import system",
                      "from os import system as int",
                      "from os import system as helper",
                      "from shutil import rmtree as cleanup"):
        source = "{}\n\ndef test_x():\n    assert True\n".format(statement)

        assert "no_escape" in rules_broken(source), source


def test_the_names_the_rules_decide_by_are_taken_from_the_rules():
    """Protected because a rule reads them, not because someone listed them.

    A call added to FORBIDDEN_CALLS, or a constructor added to
    TOP_LEVEL_CALLS, has to be protected from rebinding by the same edit,
    or the next name is the next bypass.
    """
    for name in governance.FORBIDDEN_CALLS:
        assert name in governance.PROTECTED_NAMES, name

    for spelling in governance.TOP_LEVEL_CALLS:
        assert spelling.split(".")[0] in governance.PROTECTED_NAMES, spelling

    assert governance.ALLOWED_DECORATOR[0] in governance.PROTECTED_NAMES


def test_importing_the_modules_the_templates_import_is_still_allowed():
    """Tightening this must not reject the imports the generators emit.

    "import os" binds os to os, which is the one way that name may be
    bound, and "import numpy as np" is an alias over a name no rule reads.
    """
    source = (
        "import asyncio\n"
        "import os\n"
        "import time\n"
        "import wave\n"
        "import numpy as np\n"
        "import pytest\n"
        "from regression import measurements\n"
        "\n"
        "X = int(float(os.getenv('HA_TEST_SECONDS', '0.125')) * 16000)\n"
        "\n"
        "@pytest.mark.category('audio')\n"
        "def test_x():\n"
        "    assert X and np and wave and time and asyncio and measurements\n"
    )

    assert governance.check_source(source) == [], governance.check_source(
        source)


def test_a_class_body_may_not_rebind_a_name_the_rules_decide_by():
    """A class body looks a name up in its own namespace before the module's.

    The tempting reading is that a method named str() or an attribute named
    max changes nothing a rule reads, because a class body binds in the
    class namespace. It binds there AND resolves there first,
    so `str = open` followed by `x = str("MARK", "w")` opens the file while
    pytest collects the module, while every rule here goes on reading `str`
    as the built-in. A class-level __getattr__ is refused for the reason a
    module-level one is: Python calls it with no call written anywhere.
    """
    source = (
        "class Recorder(object):\n"
        "    max = 3\n"
        "\n"
        "    def str(self):\n"
        "        return 'recorder'\n"
        "\n"
        "    def __getattr__(self, name):\n"
        "        return None\n"
        "\n\n"
        "def test_x():\n"
        "    assert Recorder().str()\n"
    )

    assert "no_top_level_code" in rules_broken(source)

    named = " ".join(v.detail for v in governance.check_source(source))

    for name in ("max", "str", "__getattr__"):
        assert name in named, "{} is not refused: {}".format(name, named)


def test_a_class_may_still_define_init_and_plain_constants():
    """The allowlist has to leave an ordinary class usable.

    __init__ is the one dunder a class body may bind: it runs when the class
    is instantiated, which in a generated module is inside a test.
    """
    source = (
        "class Recorder(object):\n"
        '    """Collects rows."""\n'
        "\n"
        "    NAME = 'recorder'\n"
        "\n"
        "    def __init__(self):\n"
        "        self.rows = []\n"
        "\n"
        "    def write(self, row):\n"
        "        self.rows.append(row)\n"
        "\n\n"
        "def test_x():\n"
        "    recorder = Recorder()\n"
        "    recorder.write(1)\n"
        "    assert recorder.rows\n"
    )

    assert governance.check_source(source) == [], governance.check_source(
        source)


# Each statement below runs its payload while pytest collects the module,
# and none of it is a call the earlier rules can read: a class body resolves
# its own names first, Python calls a dunder of its own accord, and a
# subscript is a call with the parentheses left off. A rule that lists what
# it refuses cannot cover them, because the list is of the shapes somebody
# has already thought of. Each entry is (label, statement).
IMPORT_TIME_BYPASSES = [
    # (a) A class body binds AND resolves in the class namespace.
    ("a class body assigning over an allowed call",
     'class Recorder(object):\n'
     '    str = open\n'
     '    x = str("MARK", "w")'),
    ("a class body defining over an allowed call",
     'class Recorder(object):\n'
     '    def str(x):\n'
     '        return open("MARK", "w")\n'
     '    y = str(1)'),
    ("a class body annotating over an allowed call",
     'class Recorder(object):\n'
     '    str: int = open\n'
     '    x = str("MARK", "w")'),

    # (b) Subclassing calls it. No call is written.
    ("__init_subclass__ and a subclass",
     'class A:\n'
     '    def __init_subclass__(cls, **kwargs):\n'
     '        open("MARK", "w")\n'
     '\n'
     'class B(A):\n'
     '    pass'),

    # (c) A[0] calls it. Again no call is written.
    ("__class_getitem__ and a subscript",
     'class A:\n'
     '    def __class_getitem__(cls, item):\n'
     '        open("MARK", "w")\n'
     '        return cls\n'
     '\n'
     'X = A[0]'),

    # (d) pytest looks names up on a module it has just collected.
    ("a module __dir__",
     'def __dir__():\n'
     '    open("MARK", "w")\n'
     '    return []'),

    # (e) The same hook, reached by an import rather than a def.
    ("an import bound to a module hook",
     'from os import makedirs as __getattr__'),

    # (f) The class body then resolves str through the replacement.
    ("__builtins__ replaced under a class body",
     '__builtins__ = {"str": open, "__build_class__": __build_class__}\n'
     'class Recorder:\n'
     '    x = str("MARK", "w")'),

    # (g) numpy exports max, min, abs, round and bool, and this binds them
    # all without naming one.
    ("a star import over the names the rules read",
     'from numpy import *'),
]


@pytest.mark.parametrize("statement", [
    pytest.param(statement, id=label)
    for label, statement in IMPORT_TIME_BYPASSES
])
def test_the_allowlist_rejects_what_a_list_of_refusals_cannot(statement):
    """Rule 7 allows a named set of shapes and refuses everything else.

    A generated module is a docstring, a fixed set of imports, defs with
    ordinary names and constants -- and that is the whole of it. Each
    statement here is outside that set, so none of them has to be
    anticipated to be refused.
    """
    source = "{}\n\ndef test_x():\n    assert True\n".format(statement)

    assert "no_top_level_code" in rules_broken(source), source


def test_a_class_the_module_defines_may_not_be_called_or_subscripted():
    """Both run the class's own code while pytest is collecting the file."""
    for statement in ("X = A[0]", "X = A()", "X = A[0:2]"):
        source = ("class A:\n    pass\n\n{}\n\n"
                  "def test_x():\n    assert True\n".format(statement))

        assert "no_top_level_code" in rules_broken(source), statement


@pytest.mark.parametrize("statement", [
    # Not on the import list at all.
    "import sys",
    "import pathlib",
    "import subprocess",
    "from pathlib import Path",
    "from shutil import rmtree",

    # On the list, under a name it may not be bound to.
    "import numpy",
    "import os as operating_system",
    "import pytest as pt",
    "import time as os",

    # A star import binds whatever the other module exports.
    "from numpy import *",
    "from os import *",

    # An alias is a rename of something that is not the module's to rename.
    "from os import getenv as read_env",
    "from regression import measurements as m",

    # Beyond the surface of os the templates use.
    "from os import makedirs",
    "from os import makedirs as __getattr__",

    # A relative import reaches whatever sits beside the module.
    "from . import helpers",
])
def test_only_the_imports_the_templates_need_are_allowed(statement):
    """The import list is an allowlist, so an unforeseen module is refused.

    An import decides what every later rule reads: it is the statement that
    says what os, pytest and np mean in this file. The templates need
    pytest, numpy as np, os, time, asyncio, wave and the regression package,
    each bound to one name, and nothing else is a shape a generator writes.
    """
    source = "{}\n\ndef test_x():\n    assert True\n".format(statement)

    assert "no_top_level_code" in rules_broken(source), source


@pytest.mark.parametrize("statement", [
    "__getattr__ = print",
    "def __getattr__(name):\n    return 1",
    "def __dir__():\n    return []",
    "async def __getattr__(name):\n    return 1",
    "__builtins__ = {}",
    "__test__ = {}",
    "__path__ = ['.']",
    "class __Hook__:\n    pass",
    "X = (__loader__ := 1)",
    "__all__, OTHER = ['x'], 1",
])
def test_binding_a_dunder_at_module_level_is_rejected(statement):
    """Python and pytest call dunders without a call being written.

    A module-level __getattr__ runs for any name the module does not define
    and __dir__ runs when pytest lists it, so either one is a body that
    executes during collection. There are more such hooks than a list of
    names keeps up with, so the shape is the rule instead.
    """
    source = "{}\n\ndef test_x():\n    assert True\n".format(statement)

    assert "no_top_level_code" in rules_broken(source), source


@pytest.mark.parametrize("statement", [
    "class A:\n    def __init_subclass__(cls):\n        pass",
    "class A:\n    def __class_getitem__(cls, item):\n        return cls",
    "class A:\n    def __set_name__(self, owner, name):\n        pass",
    "class A:\n    def __getattr__(self, name):\n        return None",
    "class A:\n    __slots__ = ()",
    "class A:\n    __init_subclass__ = print",
])
def test_a_class_body_may_not_bind_a_dunder_either(statement):
    """A class body is import-time code, and dunders on it are called too.

    __init_subclass__ runs when anything subclasses the class and
    __class_getitem__ runs on a subscript, so both are bodies that execute
    at import with no call in the file. __init__ is the exception, because
    it runs when a test instantiates the class.
    """
    source = "{}\n\ndef test_x():\n    assert True\n".format(statement)

    assert "no_top_level_code" in rules_broken(source), source


@pytest.mark.parametrize("statement", [
    "class A:\n    import os",
    "class A:\n    class B:\n        pass",
    "class A:\n    for _ in range(3):\n        pass",
    "class A:\n    if True:\n        NAME = 1",
])
def test_a_class_body_may_only_define_methods_and_constants(statement):
    """The same allowlist, in the other scope a module executes at import."""
    source = "{}\n\ndef test_x():\n    assert True\n".format(statement)

    assert "no_top_level_code" in rules_broken(source), source


@pytest.mark.parametrize("call", [
    # Rule 5 reads a callee by how it is written, and getattr writes the
    # name somewhere the rule cannot read it.
    'getattr(os, "sys" + "tem")("echo hi")',
    'getattr(os, chosen)("echo hi")',
    'getattr(os, "system")("echo hi")',
    'getattr(os, "popen")("echo hi")',

    # And the escapes a list of names does not happen to hold.
    'os.posix_spawn("/bin/sh", ["sh"], {})',
    'os.spawnv(os.P_WAIT, "/bin/sh", ["sh"])',
    'os.startfile("payload.exe")',
    'os.execv("/bin/sh", ["sh"])',
    'os.makedirs("PWNED")',
    'asyncio.create_subprocess_shell("echo hi")',
    'asyncio.create_subprocess_exec("sh", "-c", "echo hi")',
])
def test_a_test_that_reaches_the_system_by_another_name_is_rejected(call):
    """Rule 5 says a generated test does not shell out, so it has to hold.

    Every spelling here reaches a process or the filesystem while matching
    no name on any list of refusals: three of them are os functions nobody
    listed, two are asyncio's own subprocess API, and the rest hide the name
    inside getattr. What os, asyncio, time and wave may be used for is an
    allowlist, and an attribute name that is not a literal cannot be read at
    all, so it is refused rather than guessed at.
    """
    source = "import asyncio\nimport os\n\ndef test_x():\n    {}\n" \
             "    assert True\n".format(call)

    assert "no_escape" in rules_broken(source), call


def test_the_module_attributes_the_templates_use_are_still_allowed():
    """The other half: the allowlist must not reject the real output.

    These are every attribute of os, asyncio, time and wave that the two
    generators name, taken from their output rather than from memory.
    """
    source = (
        "import asyncio\n"
        "import os\n"
        "import time\n"
        "import wave\n"
        "\n"
        "SECONDS = float(os.getenv('HA_TEST_SECONDS', '0.125'))\n"
        "\n"
        "def test_x():\n"
        "    started = time.time()\n"
        "    loop = asyncio.get_event_loop()\n"
        "    loop.run_until_complete(asyncio.sleep(SECONDS))\n"
        "    loop.run_until_complete(\n"
        "        asyncio.gather(asyncio.ensure_future(asyncio.sleep(0))))\n"
        "    with wave.open('signal.wav') as handle:\n"
        "        assert handle\n"
        "    assert time.time() >= started\n"
    )

    assert governance.check_source(source) == [], governance.check_source(
        source)


# Every call below runs a process or reaches the interpreter's own machinery
# from inside a test body, and each writes a name that no rule matching a
# callee, an import or a two-deep attribute can read. Three shapes carry
# them: a dunder or a namespace builtin, which hand back a mapping the whole
# interpreter hangs off; an attribute chain whose dangerous segment sits
# further along than the second, because a package carries every module it
# imports as an attribute; and a call through a subscript, whose callee is a
# string the test builds. Each entry is (label, statement).
ESCAPES_THROUGH_A_NAME_NO_RULE_READS = [
    # A dunder read, with the key spelled so a search for "__import__"
    # misses it. Reading __builtins__ at all is the violation.
    ("__builtins__ under a built name",
     "sp = __builtins__['__im' + 'port__']('subprocess')\n"
     "    sp.run(['id'])"),

    # The same mapping, reached by a builtin instead of a dunder.
    ("globals() to the same mapping",
     "globals()['__builtins__']['__import__']('subprocess')"),

    # numpy re-exports sys, so np.sys is the module registry.
    ("sys through numpy", "np.sys.modules['subprocess'].run(['id'])"),

    # And ctypes, two segments along, ending in a call through a subscript.
    ("ctypes through numpy",
     "np.ctypeslib.ctypes.CDLL(None)['system'](b'id')"),

    # Every module in this package that imports subprocess, shutil or os
    # offers it as an attribute of the package a generated module may
    # legitimately import.
    ("subprocess through the dashboard",
     "regression.dashboard.subprocess.run(['id'])"),
    ("subprocess through git_changes",
     "regression.change_detection.git_changes.subprocess.run(['id'])"),
    # shutil.move rather than shutil.rmtree, so the module is what is
    # refused here and not the name of the call.
    ("shutil through generate_tests",
     "regression.ai_engine.generate_tests.shutil.move('a', 'b')"),
    ("os through paths", "regression.paths.os.execv('/bin/sh', ['sh'])"),

    # asyncio's process API on the loop object, which an allowed call hands
    # over: there is no module name at the root of this chain to read.
    ("asyncio's process API on the loop",
     "asyncio.get_event_loop().subprocess_exec(None, 'sh', '-c', 'id')"),

    # os four segments along a numpy chain.
    ("os through numpy's format reader",
     "np.lib.format.os.execv('/bin/sh', ['sh'])"),

    # Past the helper, into whatever the helper carries.
    ("further along a chain than a template writes",
     "np.sum.reduce([1, 2])"),
]


@pytest.mark.parametrize("statement", [
    pytest.param(statement, id=label)
    for label, statement in ESCAPES_THROUGH_A_NAME_NO_RULE_READS
])
def test_an_escape_through_a_name_no_rule_reads_is_rejected(statement):
    """Rule 5 reads a whole chain, a dunder and a subscripted callee.

    A rule that reads one attribute and the plain name under it stops at
    the second segment of a chain, and a package carries every module it
    imports as an attribute -- so the module that reaches the system sits
    one segment further along than such a rule looks. A dunder and a
    namespace builtin hand back a mapping instead, where the name is a
    string the test builds and no rule can read it at all.
    """
    source = (
        "import asyncio\nimport numpy as np\nimport regression\n\n"
        "def test_x():\n    {}\n    assert True\n".format(statement)
    )

    assert "no_escape" in rules_broken(source), source


@pytest.mark.parametrize("statement", [
    "names = globals()",
    "names = vars()",
    "names = locals()",

    # Read rather than called: the call is written on the next line, under
    # a spelling no list of refusals holds.
    "names = globals",
    "names = vars",
    "names = locals",
])
def test_reaching_a_namespace_is_rejected(statement):
    """A namespace hands back every name in it, including the forbidden ones.

    These rules match a callee by how it is written, and a name looked up
    in a mapping is written nowhere: globals()["__import__"]("subprocess")
    spells neither __import__ nor subprocess as a callee. So the names that
    hand back a namespace are refused where they are read, not only where
    they are called.
    """
    source = "def test_x():\n    {}\n    assert names\n".format(statement)

    assert "no_escape" in rules_broken(source), statement


def test_the_package_attributes_the_templates_use_are_still_allowed():
    """The other half: the surface allowlist must not reject real output.

    Every np.<name> the two generators emit, taken from their output rather
    than from memory, and the two regression modules the templates reach
    for. A name added to a template is added to PACKAGE_SURFACE with it.
    """
    source = (
        "import numpy as np\n"
        "import regression\n"
        "\n"
        "def test_x():\n"
        "    raw = np.frombuffer(b'\\x00\\x01', dtype=np.int16)\n"
        "    tone = np.sin(np.linspace(0.0, np.pi, 16))\n"
        "    both = np.concatenate([raw.astype(np.float32), tone])\n"
        "    level = 20.0 * np.log10(np.sqrt(np.mean(np.abs(both) ** 2)))\n"
        "    crossings = np.sum(np.abs(np.sign(np.array([1, -1]))))\n"
        "\n"
        "    assert regression.measurements and regression.audio_prep\n"
        "    assert level and crossings\n"
    )

    assert governance.check_source(source) == [], governance.check_source(
        source)


def test_a_constant_may_read_the_environment_only_as_os_getenv():
    """Only os.getenv is accepted, not any callee named getenv.

    int(float(os.getenv(...))) is what the templates emit and is legal. A
    bare getenv() -- or some object's getenv -- is a call to whatever the
    module has put under that name, which is not reading the environment.
    """
    allowed = ('import os\nX = int(float(os.getenv("HA_TEST_SECONDS", "0.1"))'
               ' * 16000)\n\ndef test_x():\n    assert X\n')

    assert governance.check_source(allowed) == []

    for spelling in ("getenv('HA_TEST_SECONDS')",
                     "engine.getenv('HA_TEST_SECONDS')"):
        source = "X = {}\n\ndef test_x():\n    assert X\n".format(spelling)

        assert "no_top_level_code" in rules_broken(source), spelling


@pytest.mark.parametrize("statement", [
    # The decorator the templates emit, with and without arguments.
    '@pytest.mark.category("audio")\ndef test_y():\n    assert True',
    '@pytest.mark.slow\ndef test_y():\n    assert True',
    '@pytest.mark.parametrize("n", [1, 2])\ndef test_y(n):\n    assert n',

    # Literal defaults: generate_tests emits tone(samples=2000, freq=440.0).
    "def tone(amplitude, samples=2000, freq=440.0):\n    return amplitude",
    "def helper(*, flag=True, name='x'):\n    return flag, name",

    # A class, which one of the emitted shapes contains.
    "class Recorder(object):\n    pass",
    "class Recorder(object):\n    NAME = 'recorder'\n\n"
    "    def write(self, path):\n        return open(path, 'w')",
])
def test_the_definition_shapes_the_templates_emit_are_accepted(statement):
    """Tightening rule 7 must not reject what the generators really write."""
    source = "import pytest\n\n{}\n\ndef test_x():\n    assert True\n".format(
        statement)

    assert governance.check_source(source) == [], source


def test_a_freshly_generated_module_breaks_no_rule(tmp_path, monkeypatch):
    """A rule tightened here is measured against real generated bytes.

    The module in the tree, src/regression/generated_tests/
    test_ai_generated.py, is gitignored and untracked, so reading it would
    make this test skip in every clean clone and in CI, where the unit step
    runs before the generate step. The point is to measure real output, so
    the module is generated here: rules mode, so no model is called and no
    key is needed, and into tmp_path, so nothing in the tree is touched.
    """
    import os

    from regression.ai_engine import generate_tests as generate_tests_module

    module = tmp_path / "test_ai_generated.py"

    monkeypatch.setenv("HA_AI", "rules")
    monkeypatch.setenv("HA_TEST_SECONDS", "0.125")
    monkeypatch.setattr(generate_tests_module, "GENERATED_TEST_PATH",
                        str(module))

    generate_tests_module.generate_tests(
        metrics={"memory": 0.52, "sync": 11.0, "retry": 0, "battery": 96,
                 "unmeasured": ["power"]},
        change_info="the compressor release constant was retuned",
    )

    assert os.path.exists(str(module)), "the generator wrote nothing"

    assert governance.check_file(str(module)) == [], governance.check_file(
        str(module))


def test_every_scenario_template_together_breaks_no_rule(
        tmp_path, monkeypatch):
    """The planner's generator, with every template it can emit, measured.

    A plan selects a subset of the scenarios, so an ordinary generated
    module exercises only the blocks that subset names. Forcing all of them
    in puts the whole vocabulary through the acceptance rules at once: the
    21 tests, the eight helper defs, the constants and the METRICS literal.

    The helpers are allowed by shape -- any def whose name is not a dunder
    -- so this cannot go stale when a template gains a ninth.
    """
    import os

    from regression.ai_engine import generate_tests as generate_tests_module
    from regression.ai_engine import planner

    module = tmp_path / "test_ai_generated.py"

    monkeypatch.setenv("HA_AI", "rules")
    monkeypatch.setenv("HA_TEST_SECONDS", "0.125")
    monkeypatch.setattr(generate_tests_module, "GENERATED_TEST_PATH",
                        str(module))

    def every_scenario(*args, **kwargs):
        plan = planner.plan_regression(*args, **kwargs)
        plan.scenarios = sorted(planner.KNOWN_SCENARIOS)
        plan.intensity = "high"

        return plan

    monkeypatch.setattr(generate_tests_module, "plan_regression",
                        every_scenario)

    plan = generate_tests_module.generate_tests(
        metrics={"memory": 0.52, "sync": 11.0, "retry": 0, "battery": 96,
                 "power": 40, "unmeasured": []},
        change_info="the compressor release constant was retuned",
    )

    assert os.path.exists(str(module)), "the generator wrote nothing"
    assert plan.selection["left_out"] == [], (
        "a scenario template never reached the module")
    assert plan.selection["selected"] == plan.selection["full"]

    assert governance.check_file(str(module)) == [], governance.check_file(
        str(module))


def test_the_canonical_suite_from_the_shipped_documents_breaks_no_rule(
        tmp_path):
    """The other generator's real output, from the documents this repo ships.

    generate_from_canonical emits a different module from the planner's: a
    different header, a different set of helpers (run, payload,
    require_loopback) and a case for every requirement and every
    release-note change, runnable and skipped. Both have to come out clean,
    so both are measured, and from the real inputs rather than a sample of
    them.
    """
    import os

    from regression import paths
    from regression.ai_engine import canonical, generate_from_canonical

    requirements = paths.default_requirements()
    note = paths.default_release_note()

    assert requirements and os.path.exists(requirements), requirements
    assert note and os.path.exists(note), note

    with open(note, encoding="utf-8", errors="replace") as handle:
        note_text = handle.read()

    document = canonical.build(note_text, requirements)

    assert document["tests"], "the shipped documents yielded no cases"

    module = str(tmp_path / "test_from_canonical.py")
    generate_from_canonical.generate(document, module)

    assert governance.check_file(module) == [], governance.check_file(module)


def test_an_unbounded_loop_is_rejected():
    """An unattended run would hang here forever."""
    source = "def test_x():\n    while True:\n        pass\n"

    assert "must_terminate" in rules_broken(source)


def test_broken_syntax_is_rejected():
    assert "parses" in rules_broken("def test_x(:\n")


# --------------------------------------------------------------------------
# What must be accepted
# --------------------------------------------------------------------------

def test_a_normal_test_passes():
    source = "def test_x():\n    assert 1 == 1\n"

    assert governance.check_source(source) == []


def test_a_skipping_test_passes():
    """Skipping is a legitimate verdict, not a test that cannot fail."""
    source = (
        "import pytest\n\n"
        "def test_x():\n"
        "    if True:\n"
        "        pytest.skip('no hardware')\n"
    )

    assert governance.check_source(source) == []


def test_a_raises_block_counts_as_an_assertion():
    source = (
        "import pytest\n\n"
        "def test_x():\n"
        "    with pytest.raises(ValueError):\n"
        "        int('x')\n"
    )

    assert governance.check_source(source) == []


def test_a_bounded_loop_passes():
    source = (
        "def test_x():\n"
        "    while True:\n"
        "        break\n"
        "    assert True\n"
    )

    assert "must_terminate" not in rules_broken(source)


# --------------------------------------------------------------------------
# Lifecycle
# --------------------------------------------------------------------------

def test_lifecycle_allows_the_intended_path():
    assert governance.can_transition(governance.DRAFT, governance.REVIEWED)
    assert governance.can_transition(governance.REVIEWED, governance.APPROVED)
    assert governance.can_transition(governance.APPROVED, governance.ACTIVE)
    assert governance.can_transition(governance.ACTIVE, governance.QUARANTINED)


def test_a_draft_cannot_skip_straight_to_active():
    """Approval is the point; letting generation reach ACTIVE defeats it."""
    assert not governance.can_transition(governance.DRAFT, governance.ACTIVE)
    assert not governance.can_transition(governance.DRAFT, governance.APPROVED)


def test_deprecated_is_terminal():
    for state in governance.LIFECYCLE:
        assert not governance.can_transition(governance.DEPRECATED, state)


def test_unknown_states_are_rejected():
    with pytest.raises(ValueError):
        governance.can_transition("invented", governance.ACTIVE)


# --------------------------------------------------------------------------
# Approval is of content, not of a filename
# --------------------------------------------------------------------------

def _isolate(tmp_path, monkeypatch):
    """Point the approval record at a temp file."""
    record = tmp_path / "approvals.json"

    monkeypatch.setattr(governance, "_record_path", lambda: str(record))

    return record


def test_approval_is_recorded_against_the_modules_test_code(
        tmp_path, monkeypatch):
    """The fingerprint covers the test code, not the readings.

    governance.RUN_SPECIFIC leaves the run's METRICS line out, so two runs
    of the same tests that measured different numbers share one approval.
    """
    _isolate(tmp_path, monkeypatch)

    module = tmp_path / "test_generated.py"
    module.write_text(
        "METRICS = {'memory': 0.52}\n\n\ndef test_x():\n    assert True\n",
        encoding="utf-8")

    governance.approve(str(module), approved_by="an approver")

    assert governance.approval_for(str(module)) is not None

    # The same tests, the next run's readings: the approval still stands.
    module.write_text(
        "METRICS = {'memory': 0.61}\n\n\ndef test_x():\n    assert True\n",
        encoding="utf-8")

    assert governance.approval_for(str(module)) is not None


def test_editing_an_approved_module_invalidates_it(tmp_path, monkeypatch):
    """Otherwise approval means 'a file by this name was once reviewed'."""
    _isolate(tmp_path, monkeypatch)

    module = tmp_path / "test_generated.py"
    module.write_text("def test_x():\n    assert True\n", encoding="utf-8")

    governance.approve(str(module), approved_by="an approver")

    module.write_text(
        "def test_x():\n    assert True\n\ndef test_y():\n    assert True\n",
        encoding="utf-8",
    )

    assert governance.approval_for(str(module)) is None


def test_approval_requires_a_name(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)

    module = tmp_path / "test_generated.py"
    module.write_text("def test_x():\n    assert True\n", encoding="utf-8")

    with pytest.raises(ValueError, match="approved_by"):
        governance.approve(str(module), approved_by="")


def test_a_rule_breaking_module_cannot_be_approved(tmp_path, monkeypatch):
    """A signature on a test that cannot fail is worse than no signature."""
    _isolate(tmp_path, monkeypatch)

    module = tmp_path / "test_generated.py"
    module.write_text("def test_x():\n    pass\n", encoding="utf-8")

    with pytest.raises(ValueError, match="acceptance rules"):
        governance.approve(str(module), approved_by="an approver")


# --------------------------------------------------------------------------
# Enforcement
# --------------------------------------------------------------------------

def test_enforce_raises_when_approval_is_required_and_absent(
        tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    monkeypatch.setenv(governance.REQUIRE_APPROVAL_ENV, "1")

    module = tmp_path / "test_generated.py"
    module.write_text("def test_x():\n    assert True\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="no approval"):
        governance.enforce(str(module))


def test_enforce_is_advisory_by_default(tmp_path, monkeypatch):
    """A bench must stay frictionless; only quoted results need the gate."""
    _isolate(tmp_path, monkeypatch)
    monkeypatch.delenv(governance.REQUIRE_APPROVAL_ENV, raising=False)

    module = tmp_path / "test_generated.py"
    module.write_text("def test_x():\n    assert True\n", encoding="utf-8")

    assert governance.enforce(str(module)) == governance.REVIEWED


def test_enforce_refuses_a_broken_module_under_enforcement(
        tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    monkeypatch.setenv(governance.REQUIRE_APPROVAL_ENV, "1")

    module = tmp_path / "test_generated.py"
    module.write_text("def test_x():\n    pass\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="acceptance rule"):
        governance.enforce(str(module))


def test_every_generated_module_is_governed(tmp_path, monkeypatch):
    """Both modules are checked, not just the planner's."""
    from regression.ai_engine import orchestrator

    checked = []

    monkeypatch.setattr(orchestrator.governance, "enforce",
                        lambda path: checked.append(path) or governance.REVIEWED)

    orchestrator.govern(["a.py", "b.py"])

    assert checked == ["a.py", "b.py"]


def test_generated_modules_lists_both_suites(monkeypatch):
    from regression.ai_engine import orchestrator
    from regression.paths import CANONICAL_TEST_PATH, GENERATED_TEST_PATH

    monkeypatch.setattr(orchestrator, "build_canonical_suite",
                        lambda: CANONICAL_TEST_PATH)

    assert orchestrator.generated_modules() == [
        GENERATED_TEST_PATH, CANONICAL_TEST_PATH]

    # No requirements and no release note: only the planner's module.
    monkeypatch.setattr(orchestrator, "build_canonical_suite", lambda: "")

    assert orchestrator.generated_modules() == [GENERATED_TEST_PATH]


def test_the_escalated_rerun_is_governed_again():
    """Approval is of content, and the re-run generates new content.

    Structural check: run_pipeline must govern the modules it is about to
    run, both before the first run and after regenerating for the re-run.
    """
    import ast
    import inspect

    from regression.ai_engine import orchestrator

    source = inspect.getsource(orchestrator.run_pipeline)
    tree = ast.parse(textwrap.dedent(source))

    calls = [node for node in ast.walk(tree)
             if isinstance(node, ast.Call)
             and getattr(node.func, "id", "") == "govern"]

    assert len(calls) == 2, "run_pipeline governs {} time(s)".format(len(calls))

    runs = [node for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and getattr(node.func, "id", "") == "run_suite"]

    # Every run is given an explicit target list, so pytest cannot execute a
    # module that governance never saw.
    for node in runs:
        assert any(kw.arg == "targets" for kw in node.keywords), ast.dump(node)


def test_clearing_requirements_leaves_them_out_of_the_run(monkeypatch, tmp_path):
    """The dashboard's Clear button sends an empty HA_REQUIREMENTS.

    An empty value means no requirements, not the default document.
    """
    from regression.ai_engine import orchestrator

    default = tmp_path / "REQUIREMENTS.md"
    default.write_text("REQ-BLE-001: The device shall advertise.\n",
                       encoding="utf-8")

    monkeypatch.setattr("regression.paths.default_requirements",
                        lambda: str(default))

    monkeypatch.delenv("HA_REQUIREMENTS", raising=False)
    assert orchestrator.requirements_path() == str(default)

    monkeypatch.setenv("HA_REQUIREMENTS", "")
    assert orchestrator.requirements_path() == ""

    monkeypatch.setenv("HA_REQUIREMENTS", "  other.md  ")
    assert orchestrator.requirements_path() == "other.md"


def test_clearing_both_inputs_generates_no_canonical_suite(monkeypatch):
    from regression.ai_engine import orchestrator

    monkeypatch.setenv("HA_REQUIREMENTS", "")
    monkeypatch.setenv("HA_RELEASE_NOTE", "")

    assert orchestrator.build_canonical_suite() == ""


def test_a_refused_escalation_records_the_plan_that_actually_ran():
    """The refusal path must not file the first run under the second plan.

    Structural check, because reaching this branch for real needs a board
    and an approval store. run_pipeline regenerates at the higher intensity
    before governing the result; when governance refuses, nothing at that
    intensity ran, so `plan` must still be the first run's.
    """
    import ast
    import inspect

    from regression.ai_engine import orchestrator

    source = inspect.getsource(orchestrator.run_pipeline)
    tree = ast.parse(textwrap.dedent(source))

    handlers = [node for node in ast.walk(tree)
                if isinstance(node, ast.Try) and node.handlers and node.orelse]

    assert handlers, "the escalation is not a try/except/else"

    for node in handlers:
        # The escalated plan may only become `plan` in the else: branch --
        # the one reached when governance did NOT refuse.
        for branch, allowed in (("handlers", False), ("orelse", True)):
            body = (node.handlers if branch == "handlers" else node.orelse)

            assigned = [
                target.id
                for stmt in body
                for sub in ast.walk(stmt)
                if isinstance(sub, ast.Assign)
                for target in sub.targets
                if isinstance(target, ast.Name)
            ]

            if allowed:
                assert "plan" in assigned, (
                    "the escalated plan is never recorded after a successful "
                    "re-run")
            else:
                assert "plan" not in assigned, (
                    "the refusal branch overwrites plan, so a refused "
                    "escalation is recorded at the intensity that never ran")


def test_the_entry_points_can_start():
    """Both mains run their output guard, and --help exits cleanly.

    _resilient_output() runs before the first line of work, and no other
    test here calls main(). Touching each entry point is cheap; finding a
    name it uses that its module does not import from a traceback is not.
    """
    import subprocess
    import sys as _sys

    from regression import dashboard
    from regression.ai_engine import orchestrator

    # The guard itself, in both modules.
    orchestrator._resilient_output()
    dashboard._resilient_output()

    # And each entry point as a process, which is how they actually run.
    for module in ("regression.ai_engine.orchestrator", "regression.dashboard"):
        done = subprocess.run(
            [_sys.executable, "-m", module, "--help"],
            capture_output=True, text=True, timeout=120)

        assert done.returncode == 0, "{} --help exited {}: {}".format(
            module, done.returncode, done.stderr[-400:])
        assert "usage" in (done.stdout + done.stderr).lower()


# --------------------------------------------------------------------------
# What a generated module may call, and what may be left out of its hash
# --------------------------------------------------------------------------

@pytest.mark.parametrize("call", [
    "subprocess.run(['true'])",
    "subprocess.Popen(['true'])",
    "shutil.rmtree('/')",
    "os.remove('PWNED')",
    "os.unlink('PWNED')",
    "__import__('shutil').rmtree('/')",
])
def test_a_test_that_reaches_outside_the_harness_is_rejected(call):
    """A short list of refusals covers only the names already on it.

    Each call here starts a process, deletes a file or imports by name, so
    FORBIDDEN_CALLS and FORBIDDEN_IMPORTS between them have to reach all of
    it.
    """
    source = "def test_x():\n    {}\n    assert True\n".format(call)

    assert rules_broken(source) & {"no_escape", "no_network"}, (
        "{} passed the acceptance rules".format(call))


def test_the_helpers_a_generated_module_really_uses_are_still_allowed():
    """The other half: widening the list must not reject the real output.

    run() is the emitted coroutine helper and every generated test calls it,
    so "run" cannot go on the list however much subprocess.run wants it
    there.
    """
    source = (
        "def run(device, coro):\n"
        "    return coro\n"
        "\n"
        "def test_x(ble_device):\n"
        "    value = run(ble_device, ble_device.read_battery())\n"
        "    assert value is not None\n"
    )

    assert rules_broken(source) == set()


def test_code_cannot_hide_in_the_line_excluded_from_the_fingerprint():
    """RUN_SPECIFIC blanks any line starting "METRICS = ", whatever follows.

    The exclusion exists for a dict of this run's readings. Anything a
    literal cannot express is code, and code has to be hashed or an
    approval covers bytes nobody read.
    """
    readings = 'METRICS = {"power": 40, "memory": 3.5}\n'
    hostile = 'METRICS = __import__("shutil").rmtree("/") or {}\n'

    assert governance.EXCLUDED_NOTE in governance.approvable_source(readings)
    assert governance.EXCLUDED_NOTE not in governance.approvable_source(hostile)

    # And the fingerprint therefore moves when that line does.
    other = 'METRICS = __import__("os").system("calc") or {}\n'

    assert (governance.approvable_source(hostile)
            != governance.approvable_source(other))


def test_a_refused_escalation_leaves_the_module_that_ran_on_disk(tmp_path):
    """A refused escalation restores the module that ran.

    The escalation writes its own bytes over that module, and the KPI
    records the first run's plan. The helpers below put it back; this
    exercises them on real files.
    """
    from regression.ai_engine import orchestrator

    module = tmp_path / "test_ai_generated.py"
    module.write_text("INTENSITY = 'low'\n", encoding="utf-8")

    ran = orchestrator._module_bytes(str(module))

    module.write_text("INTENSITY = 'medium'\n", encoding="utf-8")

    assert orchestrator._restore_module(str(module), ran) is True
    assert module.read_text(encoding="utf-8") == "INTENSITY = 'low'\n"

    # Nothing to restore is not a failure: a first run with no module yet
    # must not raise here.
    assert orchestrator._restore_module(str(module), None) is False


def test_the_escalation_neither_rebuilds_the_suite_nor_keeps_its_bytes():
    """Structural, for the same reason as the test above it.

    generated_modules() rebuilds the requirement suite from the canonical
    document, which under HA_AI=llm is a second model call writing a
    different set of cases. It must not be called again after the escalated
    generate, and the refusal branch must restore the module that ran.
    """
    import ast
    import inspect

    from regression.ai_engine import orchestrator

    source = inspect.getsource(orchestrator.run_pipeline)
    tree = ast.parse(textwrap.dedent(source))

    def called(node):
        return {sub.func.id for sub in ast.walk(node)
                if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name)}

    escalations = [node for node in ast.walk(tree)
                   if isinstance(node, ast.If)
                   and "generate_tests" in called(node)]

    assert escalations, "the escalation branch is not an if"

    for branch in escalations:
        assert "generated_modules" not in called(branch), (
            "the escalation rebuilds the requirement suite, so the re-run "
            "executes a different set of cases from the one that failed")

    handlers = [node for node in ast.walk(tree)
                if isinstance(node, ast.Try) and node.handlers and node.orelse]

    for node in handlers:
        restored = set()

        for stmt in node.handlers:
            restored |= called(stmt)

        assert "_restore_module" in restored, (
            "a refused escalation leaves its own module on disk")


# --------------------------------------------------------------------------
# The pipeline, end to end
# --------------------------------------------------------------------------

def _orchestration_harness(monkeypatch, tmp_path, verdicts, refuse_escalation,
                      build_id="test-build+0badc0de"):
    """run_pipeline with the board, pytest and the KPI store replaced.

    Everything between them is the real thing: the risk engine, the
    planner, the escalation decision, the governance call and the module
    bookkeeping. Inspecting the source, as the other escalation tests do,
    cannot catch a step passing the wrong thing to the next one; running the
    wiring can.
    """
    from regression.ai_engine import orchestrator, planner

    module = tmp_path / "test_ai_generated.py"
    module.write_text("INTENSITY = 'unset'\n", encoding="utf-8")

    monkeypatch.setattr(orchestrator, "GENERATED_TEST_PATH", str(module))
    monkeypatch.setattr(orchestrator, "read_battery", lambda: None)
    monkeypatch.setattr(orchestrator.power_analyzer, "merge_into",
                        lambda metrics: None)
    monkeypatch.setattr(orchestrator, "new_run_dir", lambda: str(tmp_path))

    calls = {"generated": [], "governed": 0, "ran": 0, "recorded": None,
             "modules_built": 0}

    def fake_generated_modules():
        # Counted: building this list rebuilds the requirement suite, which
        # under HA_AI=llm is a second model call writing a different set of
        # cases. An escalation must not do it.
        calls["modules_built"] += 1

        return [str(module)]

    monkeypatch.setattr(orchestrator, "generated_modules",
                        fake_generated_modules)

    def fake_generate_tests(metrics, change_info, tests, risk_score=0,
                            history=None, min_intensity=None):
        plan = planner.plan_regression(
            metrics, change_info, tests, risk_score, history)

        if min_intensity and min_intensity != plan.intensity:
            plan.intensity = min_intensity

        module.write_text(
            "INTENSITY = {!r}\n".format(plan.intensity), encoding="utf-8")
        calls["generated"].append(plan.intensity)

        return plan

    def fake_govern(modules):
        calls["governed"] += 1

        if refuse_escalation and calls["governed"] > 1:
            # The refusal governance really raises, advice and all, so the
            # orchestrator's decision about what to print is under test.
            raise governance.ApprovalRequired(
                "HA_REQUIRE_APPROVAL=1 but {} has no approval.".format(
                    module.name),
                "Review it and run:\n    {}".format(
                    governance.approve_command(str(module))),
            )

        return "draft"

    class _Report:
        def __init__(self, status):
            self.status = status
            self.records = []
            self.run_dir = str(tmp_path)

    def fake_run_suite(run_dir=None, targets=None):
        status = verdicts[min(calls["ran"], len(verdicts) - 1)]
        calls["ran"] += 1

        assert targets == [str(module)], "the run executed a different module"

        return status, _Report(status)

    monkeypatch.setattr(orchestrator, "generate_tests", fake_generate_tests)
    monkeypatch.setattr(orchestrator, "govern", fake_govern)
    monkeypatch.setattr(orchestrator, "run_suite", fake_run_suite)
    monkeypatch.setattr(orchestrator.kpi, "record_run",
                        lambda report, **kw: calls.update(recorded=kw))
    monkeypatch.setattr(orchestrator.kpi, "record_pipeline_escapes",
                        lambda report, **kw: [])

    monkeypatch.setenv("HA_AI", "rules")

    if build_id:
        monkeypatch.setenv("HA_BUILD_ID", build_id)
    else:
        monkeypatch.delenv("HA_BUILD_ID", raising=False)

    result = orchestrator.run_pipeline(
        metrics={"power": 40, "memory": 3.5, "sync": 10, "retry": 0,
                 "unmeasured": []},
        change_info="documentation wording tidy-up",
    )

    return result, calls, module


def test_the_pipeline_runs_end_to_end_and_records_what_ran(
        monkeypatch, tmp_path):
    """A clean run: one generate, one govern, one suite, one KPI record."""
    result, calls, module = _orchestration_harness(
        monkeypatch, tmp_path, ["pass"], refuse_escalation=False)

    assert calls["ran"] == 1, "a passing run escalated"
    assert calls["governed"] == 1
    assert result["intensity"] == calls["generated"][0]
    assert calls["recorded"]["plan"].intensity == result["intensity"]
    assert calls["recorded"]["build_id"] == "test-build+0badc0de"
    assert "INTENSITY = {!r}".format(result["intensity"]) in module.read_text(
        encoding="utf-8")


def test_a_refused_escalation_records_and_leaves_behind_the_run_that_happened(
        monkeypatch, tmp_path):
    """The failure path, where the record and the file on disk can disagree."""
    result, calls, module = _orchestration_harness(
        monkeypatch, tmp_path, ["fail"], refuse_escalation=True)

    assert len(calls["generated"]) == 2, "the escalation did not re-plan"
    assert calls["generated"] == ["low", "medium"], calls["generated"]
    assert calls["ran"] == 1, "the refused escalation ran anyway"

    first = calls["generated"][0]

    assert result["intensity"] == first
    assert calls["recorded"]["plan"].intensity == first
    assert "INTENSITY = {!r}".format(first) in module.read_text(
        encoding="utf-8"), "the escalated module was left on disk"

    # The module list is built once. Building it again regenerates the
    # requirement suite, and under HA_AI=llm that is a second model call
    # writing a different set of cases for a run that has already happened.
    assert calls["modules_built"] == 1, (
        "the escalation rebuilt the requirement suite")


def test_a_refused_escalation_prints_the_reason_and_no_circular_advice(
        monkeypatch, tmp_path, capsys):
    """The refusal carries the approve command; here it cannot be followed.

    The escalated module is put back on the next line, so the refusal says
    it has been replaced by the one that ran and prints no approve command
    beside it.
    """
    _orchestration_harness(monkeypatch, tmp_path, ["fail"], refuse_escalation=True)

    out = capsys.readouterr().out

    assert "Escalation refused:" in out
    assert "has no approval" in out
    assert "replaced by the one that ran" in out
    assert "python -m regression.governance" not in out, (
        "the refusal printed advice the next line makes impossible")


def test_the_board_is_not_asked_again_when_main_has_already_asked(
        monkeypatch, tmp_path):
    """main() says the board is asked once, so run_pipeline must not ask it
    again.

    A board that reports no build id leaves HA_BUILD_ID unset, so
    _announced_by_main is what settles it.
    """
    from regression.ai_engine import orchestrator

    asked = []

    monkeypatch.setattr(orchestrator, "read_build_id",
                        lambda: asked.append("board") or None)
    monkeypatch.setattr(orchestrator, "_announced_by_main", True)
    monkeypatch.setattr(orchestrator, "_build_id_from_main", "")

    _orchestration_harness(monkeypatch, tmp_path, ["pass"],
                      refuse_escalation=False, build_id="")

    assert asked == [], "the board was asked a second time"


def test_the_build_id_is_announced_once(monkeypatch, tmp_path, capsys):
    """The build id is announced once, by main() and not again by
    run_pipeline as an override of the device that supplied it."""
    from regression.ai_engine import orchestrator

    monkeypatch.setattr(orchestrator, "_announced_by_main", True)

    _orchestration_harness(monkeypatch, tmp_path, ["pass"],
                      refuse_escalation=False)

    out = capsys.readouterr().out

    assert "overrides the device" not in out


# --------------------------------------------------------------------------
# The command the program tells an operator to run
# --------------------------------------------------------------------------

def test_the_command_a_refusal_prints_actually_runs(tmp_path, monkeypatch):
    """The printed approve command must parse and run.

    It is the one command this program asks an operator to type, and it
    ends '--by "your name" --path X', so --path has to be accepted after
    the subcommand as well as before it. This test takes the printed
    sentence and runs it.
    """
    import shlex

    _isolate(tmp_path, monkeypatch)
    monkeypatch.setenv(governance.REQUIRE_APPROVAL_ENV, "1")

    module = tmp_path / "test_generated.py"
    module.write_text("def test_x():\n    assert True\n", encoding="utf-8")

    with pytest.raises(governance.ApprovalRequired) as raised:
        governance.enforce(str(module))

    # The reason alone says nothing a caller would have to contradict.
    assert "python -m" not in raised.value.reason

    command = raised.value.advice.splitlines()[-1].strip()

    assert command.startswith("python -m regression.governance ")

    argv = [word.strip('"') for word in
            shlex.split(command, posix=False)[3:]]

    assert governance.main(argv) == 0
    assert governance.approval_for(str(module)) is not None


def test_the_printed_command_survives_a_space_in_the_path(
        tmp_path, monkeypatch):
    """"exactly as it has to be typed" has to hold for "C:/Users/Jane Doe".

    It is not a rare shape: base_dir() falls back to LOCALAPPDATA when the
    application cannot write beside itself, and LOCALAPPDATA carries the
    Windows account name.

    The printed sentence is split the way the platform's own shell splits
    it, and the argv that comes out has to name the module as ONE argument
    and approve it.
    """
    import os
    import shlex

    _isolate(tmp_path, monkeypatch)

    folder = tmp_path / "Jane Doe" / "generated"
    folder.mkdir(parents=True)

    module = folder / "test_generated.py"
    module.write_text("def test_x():\n    assert True\n", encoding="utf-8")

    command = governance.approve_command(str(module))

    if os.name == "nt":
        words = [w.strip('"') for w in shlex.split(command, posix=False)]
    else:
        words = shlex.split(command)

    assert words[3] == "--path"
    assert words[4] == str(module), "the path is not one argument"

    assert governance.main(words[3:]) == 0
    assert governance.approval_for(str(module)) is not None


def test_path_is_accepted_on_either_side_of_the_subcommand(
        tmp_path, monkeypatch):
    """Whichever way it is written, so the printed advice cannot be wrong."""
    _isolate(tmp_path, monkeypatch)

    module = tmp_path / "test_generated.py"
    module.write_text("def test_x():\n    assert True\n", encoding="utf-8")

    assert governance.main(["--path", str(module), "status"]) == 0
    assert governance.main(["status", "--path", str(module)]) == 0

    assert governance.main(
        ["--path", str(module), "approve", "--by", "an approver"]) == 0
    assert governance.approval_for(str(module)) is not None

    # A --path before the subcommand is still not lost to the subcommand's
    # own default, which is the trap this arrangement sets.
    assert governance.main(["--path", "no/such/module.py", "status"]) == 1
