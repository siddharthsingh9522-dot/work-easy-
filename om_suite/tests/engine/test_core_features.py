import pandas as pd
from dbp import report_builder


def test_duplicate_branch_code_is_merged():
    data = {
        "SUMMARY_PIVOT": [
            {"branch_code": 801, "branch_name": "TATA MOTORS", "category": "DDR_PENDING", "total": 6},
            {"branch_code": "801", "branch_name": "TATA MOTORS", "category": "DDR_PENDING", "total": 34},
            {"branch_code": 801, "branch_name": "KOLKATA", "category": "PAID", "total": 2},
        ]
    }
    out = report_builder.build_summary(data, {}, {801: "HIDE ROAD"})
    rows = out[pd.to_numeric(out["B.CODE"], errors="coerce") == 801]
    assert len(rows) == 1
    assert int(rows.iloc[0]["DDR PENDING"]) == 40
    assert rows.iloc[0]["B.NAME"] == "HIDE ROAD"


def test_ddr_raw_text_number_buckets_are_normalized():
    df = pd.DataFrame({
        "DLY_CHALLAN_FROM": [801, "801", 801, "9998"],
        "DLY_CHALLAN": [8012612000001, "8012612000002", 8012612000003, "8112612000004"],
    })
    counts, unresolved, decoded = report_builder.count_ddr_by_branch(df)
    assert int(counts.loc[801]) == 3
    assert int(counts.loc[811]) == 1
    assert unresolved == 0
    assert decoded == 1
