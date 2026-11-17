"""FAQ 行 → 检索 chunk 的形状。"""
from app.kb.loader import faq_chunks


def test_faq_chunks_shape():
    chunks = faq_chunks(
        [
            {
                "id": 7,
                "question": "价保规则是什么",
                "answer": "签收后 7 天内可申请补差。",
            }
        ]
    )

    assert chunks == [
        {
            "id": "faq-7",
            "text": "价保规则是什么\n签收后 7 天内可申请补差。",
            "source": "faq",
            "section": "价保规则是什么",
            "kind": "faq",
        }
    ]


def test_faq_chunks_multiple_and_empty():
    assert faq_chunks([]) == []
    assert [c["id"] for c in faq_chunks(
        [
            {"id": 1, "question": "q1", "answer": "a1"},
            {"id": 2, "question": "q2", "answer": "a2"},
        ]
    )] == ["faq-1", "faq-2"]
