import json

from ezwipe.diag.cli import main


def test_simulated_diagnostic_writes_report(tmp_path, capsys):
    assert main(["--simulate", "--out", str(tmp_path)]) == 0
    [report] = list(tmp_path.glob("ezwipe-diagnostic-*.json"))
    data = json.loads(report.read_text())
    assert data["environment"]["simulated"] is True
    assert len(data["drives"]) == 8
    assert "PROTECTED" in capsys.readouterr().out
