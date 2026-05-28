from capture_to_notion.blocks import build_body_blocks, split_block_batches


def block_text(block):
    block_type = block["type"]
    return block[block_type]["rich_text"][0]["text"]["content"]


def test_build_body_blocks_converts_headings_lists_quotes_code_and_paragraphs():
    raw = """# Title ignored by caller

## Why V4 matters

DeepSeek V4 changes the cost model.

- 1M context
- cache pricing

> Models become workflow infrastructure.

```text
Flash for default work
Pro for hard calls
```

---

Final paragraph."""

    blocks = build_body_blocks(raw, title="Title ignored by caller")

    assert [block["type"] for block in blocks] == [
        "heading_2",
        "paragraph",
        "bulleted_list_item",
        "bulleted_list_item",
        "quote",
        "code",
        "divider",
        "paragraph",
    ]
    assert block_text(blocks[0]) == "Why V4 matters"
    assert block_text(blocks[1]) == "DeepSeek V4 changes the cost model."
    assert blocks[5]["code"]["language"] == "plain text"
    assert block_text(blocks[7]) == "Final paragraph."


def test_build_body_blocks_omits_matching_title_line():
    blocks = build_body_blocks("标题：DeepSeek V4\n\nBody", title="DeepSeek V4")

    assert [block["type"] for block in blocks] == ["paragraph"]
    assert block_text(blocks[0]) == "Body"


def test_build_body_blocks_splits_long_rich_text_chunks():
    blocks = build_body_blocks("x" * 4500, title="Long")

    assert len(blocks) == 3
    assert [len(block_text(block)) for block in blocks] == [1900, 1900, 700]


def test_split_block_batches_uses_notion_100_child_limit():
    blocks = build_body_blocks("\n\n".join(f"p{i}" for i in range(205)), title="Many")

    batches = split_block_batches(blocks)

    assert [len(batch) for batch in batches] == [100, 100, 5]
