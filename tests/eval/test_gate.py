from app.eval.parsing.metrics import decide_gate, evaluate_parse


def test_evaluate_parse_scores_tables_and_order():
    predicted_tables = ["<table><tr><td>a</td></tr></table>"]
    expected_tables = ["<table><tr><td>a</td></tr></table>"]
    result = evaluate_parse(
        predicted_tables=predicted_tables,
        expected_tables=expected_tables,
        predicted_order=["Invoice", "a"],
        expected_order=["Invoice", "a"],
    )
    assert result["mean_teds"] == 1.0
    assert result["reading_order"] == 1.0


def test_evaluate_parse_missing_table_scores_zero():
    result = evaluate_parse(
        predicted_tables=[],
        expected_tables=["<table><tr><td>a</td></tr></table>"],
        predicted_order=[],
        expected_order=["a"],
    )
    assert result["mean_teds"] == 0.0


def test_gate_adopts_when_teds_up_and_ragas_flat():
    decision = decide_gate(
        baseline={"mean_teds": 0.40, "faithfulness": 0.92, "context_precision": 0.76},
        candidate={"mean_teds": 0.85, "faithfulness": 0.92, "context_precision": 0.77},
        max_ragas_regression=0.02,
    )
    assert decision["adopt"] is True


def test_gate_rejects_on_ragas_regression():
    decision = decide_gate(
        baseline={"mean_teds": 0.40, "faithfulness": 0.92, "context_precision": 0.76},
        candidate={"mean_teds": 0.99, "faithfulness": 0.85, "context_precision": 0.77},
        max_ragas_regression=0.02,
    )
    assert decision["adopt"] is False
    assert "faithfulness" in decision["reason"]
