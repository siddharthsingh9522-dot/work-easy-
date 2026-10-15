
import pandas as pd
from dbp import report_builder

def test_customer_party_sheets_count_under_party_not_branch():
    """CONFIRMED by user (2026-10-04): a docket inside a party/customer sheet
    (TATA MOTOR, VE COMMERCIAL VEHICLES LTD, etc.) must be counted under that
    PARTY's own row -- never under the branch, even when the branch code
    inside the party block matches a real branch. Each party row splits by
    category too: an "ADR MADE BUT NOT BILLED" block feeds PENDING BILLING,
    a "POD ON NET BUT NOT BILLED" block feeds POD ON NET -- same as the
    branch categories, just attributed to the party instead of the branch.
    (This matches _customer_rows_from_other_blocks()'s existing, already-
    correct implementation; OTHER_BLOCKS -- not just OTHER_BLOCKS_DATA -- is
    what that function reads, so both must be present here, same as real
    ingest.load_workbook_categorized() output.)"""
    data = {
        "PAID": pd.DataFrame({"B.CODE":[801,801], "BRANCH":["X","X"], "CN_NO":["1","2"]}),
        "TOPAY": pd.DataFrame({"B.CODE":[801], "BRANCH":["X"], "CN_NO":["3"]}),
        "PENDING_BILLING": pd.DataFrame({"ADR BRANCH":[801,801], "CN_NO":["4","4"]}),
        "POD_NOT_BILLED": pd.DataFrame({"BILL_BRANCH_CODE":[801], "CN_NO":["5"]}),
        "DDR_PENDING": pd.DataFrame({"DLY_CHALLAN_FROM":[801,"801"], "DLY_CHALLAN":[None,None]}),
        "OTHER_BLOCKS": {
            "TATA MOTORS": [
                {"branch_code": 801, "title": "ADR MADE BUT NOT BILLED", "category": "PENDING_BILLING", "row_count": 1},
                {"branch_code": 801, "title": "POD ON NET BUT NOT BILLED", "category": "POD_NOT_BILLED", "row_count": 1},
            ],
        },
        "OTHER_BLOCKS_DATA": {
            "TATA MOTORS": pd.DataFrame({"CN_NO":["4","6"], "_SECTION_BRANCH":[801,801], "_BLOCK_TITLE":["ADR","NET"]})
        },
    }
    out=report_builder.build_summary(data,{801:"X"},{801:"X"})

    branch_rows=out[pd.to_numeric(out["B.CODE"],errors="coerce")==801]
    assert len(branch_rows)==1
    # CN "4" is owned by TATA MOTORS' ADR block, so branch 801's own PENDING
    # BILLING count must NOT include it (branch's raw PENDING_BILLING frame
    # only has "4","4" -- both belong to the party, so branch count is 0).
    assert int(branch_rows.iloc[0]["PENDING BILLING"])==0
    assert int(branch_rows.iloc[0]["DDR PENDING"])==2

    party_rows=out[out["B.NAME"].astype(str).str.upper()=="TATA MOTORS"]
    assert len(party_rows)==1
    assert int(party_rows.iloc[0]["PENDING BILLING"])==1
    assert int(party_rows.iloc[0]["POD ON NET BUT NOT BILLED"])==1

def test_summary_pivot_is_not_added_to_detail_counts():
    data={
        "SUMMARY_PIVOT":[{"branch_code":801,"branch_name":"X","category":"PENDING_BILLING","total":40}],
        "PENDING_BILLING":pd.DataFrame({"ADR BRANCH":[801]*5,"CN_NO":[str(i) for i in range(5)]}),
    }
    out=report_builder.build_summary(data,{801:"X"},{801:"X"})
    row=out[pd.to_numeric(out["B.CODE"],errors="coerce")==801].iloc[0]
    assert int(row["PENDING BILLING"])==40

def test_duplicate_customer_sheet_names_can_be_merged():
    data={
        "PAID":pd.DataFrame({"B.CODE":[801],"BRANCH":["X"],"CN_NO":["1"]}),
        "OTHER_BLOCKS_DATA":{
            "TATA MOTORS":pd.DataFrame({"CN_NO":["1"],"_BLOCK_TITLE":["ADR"],"_SECTION_BRANCH":[801]}),
            "tata motors ":pd.DataFrame({"CN_NO":["2"],"_BLOCK_TITLE":["NET"],"_SECTION_BRANCH":[801]}),
        }
    }
    merged=report_builder._merge_customer_blocks(data)
    assert list(merged)==["TATA MOTORS"]
    assert set(merged["TATA MOTORS"]["CN_NO"])=={"1","2"}
