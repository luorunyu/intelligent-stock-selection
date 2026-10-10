from stock_selection.screening_schemes.plan_two.lending import (
    read_lending_file,
    resolve_lending_file,
)


def test_reads_gbk_tab_export_and_preserves_leading_zero(tmp_path):
    path = tmp_path / "20261009.xls"
    path.write_bytes(
        (
            '="市场类别"\t="证券代码"\t="证券名称"\t="可用数量"\t'
            '="保证金比例(%)"\t="融券状态"\t\n'
            '="深圳Ａ"\t="000001"\t="平安银行"\t1200\t100\t="正常"\t\n'
            '="上海Ａ"\t="600004"\t="白云机场"\t0\t100\t="正常"\t\n'
        ).encode("gb18030")
    )

    result = read_lending_file(path)

    assert result["security_code"].tolist() == ["000001", "600004"]
    assert result["ts_code"].tolist() == ["000001.SZ", "600004.SH"]
    assert result["available_quantity"].tolist() == [1200, 0]
    assert result["lending_date"].unique().tolist() == ["20261009"]


def test_resolves_standard_dated_file(tmp_path):
    target = tmp_path / "20261009.xls"
    target.write_text("test", encoding="utf-8")

    assert resolve_lending_file(
        as_of_date="20261009",
        lending_root=tmp_path,
    ) == target
