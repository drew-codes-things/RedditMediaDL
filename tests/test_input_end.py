import pytest

import main


def end_of_input(prompt=""):
    raise EOFError


def test_closed_input_ends_with_a_message_not_a_traceback(monkeypatch, capsys):
    monkeypatch.setattr("builtins.input", end_of_input)
    monkeypatch.setattr(main.sys, "argv", ["main.py"])
    monkeypatch.setattr(main, "relay_configured", lambda: False)
    with pytest.raises(SystemExit) as stopped:
        main.main()
    assert stopped.value.code == 1
    assert "Input ended" in capsys.readouterr().out
