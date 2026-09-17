import pandas as pd

from app.eval.document_rag.ragas_judge_client import summarize_ragas


def test_summarize_reports_both_bounds_and_nan_count():
    df = pd.DataFrame({"faithfulness": [1.0, float("nan"), 0.5]})
    out = summarize_ragas(df, ["faithfulness"])
    assert out["faithfulness"]["mean_optimistic"] == 0.75
    assert out["faithfulness"]["mean_pessimistic"] == 0.5
    assert out["faithfulness"]["nan_count"] == 1
