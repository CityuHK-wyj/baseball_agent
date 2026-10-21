import pytest

from app.capabilities.python_compute import run_program, validate_program


def test_valid_program_computes():
    program = "\n".join(
        [
            "a = inputs['a'][0]['home_runs']",
            "b = inputs['b'][0]['home_runs']",
            "output = [{'difference': a - b}]",
        ]
    )
    ok, output, error = run_program(program, {"a": [{"home_runs": 54}], "b": [{"home_runs": 44}]})
    assert ok, error
    assert output == [{"difference": 10}]


def test_statistics_module_allowed():
    program = "\n".join(
        [
            "import statistics",
            "vals = [r['x'] for r in inputs['a']]",
            "output = [{'mean': statistics.mean(vals)}]",
        ]
    )
    ok, output, error = run_program(program, {"a": [{"x": 1}, {"x": 3}]})
    assert ok, error
    assert output[0]["mean"] == 2


@pytest.mark.parametrize(
    "program",
    [
        "import os\noutput = os.listdir('/')",
        "output = open('/etc/passwd').read()",
        "output = __import__('os').getcwd()",
        "output = ().__class__.__bases__[0].__subclasses__()",
        "with open('/tmp/x') as f:\n    output = f.read()",
        "eval('1+1')\noutput = 1",
        "output = 1",  # missing expected name is fine; see below
    ],
)
def test_dangerous_or_incomplete_programs_rejected(program):
    result = validate_program(program)
    if "output = 1" in program and "eval" not in program:
        assert result.ok  # simple assignment is allowed
    else:
        assert not result.ok


def test_missing_output_assignment_rejected():
    assert not validate_program("x = 1").ok


def test_syntax_error_rejected():
    assert not validate_program("output = (").ok


def test_forbidden_name_in_subscript():
    result = validate_program("output = [getattr(1, 'x')]")
    assert not result.ok
