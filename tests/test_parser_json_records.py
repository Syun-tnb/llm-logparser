import logging

from llm_logparser.core.parser import iter_json_records


def test_iter_json_records_handles_bom(tmp_path):
    p = tmp_path / "test.jsonl"
    # BOM + 空白 + JSON
    content = "\ufeff  {\"foo\": 1}\n"
    p.write_text(content, encoding="utf-8")

    logger = logging.getLogger("test")
    records = list(iter_json_records(p, logger))
    assert len(records) == 1
    assert records[0]["foo"] == 1
