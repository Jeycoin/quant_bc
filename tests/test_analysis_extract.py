"""Analysis-block extraction from agent final replies."""

from agent.agent import extract_analysis_blocks


def test_extracts_single_block():
    text = '市场分析……\n```analysis\n{"symbol": "BTC", "action": "WAIT", "confidence": 0.5}\n```'
    blocks = extract_analysis_blocks(text)
    assert len(blocks) == 1
    assert blocks[0]["symbol"] == "BTC"


def test_extracts_multiple_blocks_and_skips_bad_json():
    text = (
        '```analysis\n{"symbol": "BTC"}\n```\n中间文字\n'
        '```analysis\n{"symbol": "ETH"}\n```\n'
        '```analysis\n{not json}\n```'
    )
    blocks = extract_analysis_blocks(text)
    assert [b["symbol"] for b in blocks] == ["BTC", "ETH"]


def test_no_blocks():
    assert extract_analysis_blocks("plain reply, no analysis") == []
    assert extract_analysis_blocks("") == []
