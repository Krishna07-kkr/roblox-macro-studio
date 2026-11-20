"""The old command-line entry points still start the right code (T021)."""

import os

from conftest import PROJECT_ROOT, run_in_fresh_python


def test_python_main_py_still_parses_its_arguments():
    code, output = run_in_fresh_python(os.path.join(PROJECT_ROOT, "main.py"), "--help")
    assert code == 0, output
    assert "--mode" in output


def test_goliath_runner_module_has_the_same_cli():
    code, output = run_in_fresh_python(
        os.path.join(PROJECT_ROOT, "macro_studio", "runners", "goliath.py"), "--help")
    assert code == 0, output
    assert "--mode" in output


def test_mutation_runner_script_keeps_its_entry_point():
    import mutation_runner
    from macro_studio.runners import mutation
    assert mutation_runner.main is mutation.main
