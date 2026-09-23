import pytest
from pydantic import ValidationError

from remora_mcp.schemas import (
    AgentCourseWrite,
    AgentMediaUpload,
    AgentSectionWrite,
    AgentSetWrite,
    CardWrite,
    CoursePublication,
)


def test_card_rejects_duplicate_wrong_answer() -> None:
    with pytest.raises(ValidationError):
        CardWrite(term="кот", definition="cat", wrong_definition_answers=["cat"])


def test_card_rejects_empty_wrong_answer() -> None:
    with pytest.raises(ValidationError):
        CardWrite(term="кот", definition="cat", wrong_definition_answers=["  "])


def test_card_accepts_distinct_wrong_answers() -> None:
    card = CardWrite(term="кот", definition="cat", wrong_definition_answers=["dog", "bird"])
    assert card.wrong_definition_answers == ["dog", "bird"]


def test_media_upload_requires_exactly_one_source() -> None:
    with pytest.raises(ValidationError):
        AgentMediaUpload()
    with pytest.raises(ValidationError):
        AgentMediaUpload(source_url="https://example.com/a.png", data_base64="Zm9v", mime="image/png")


def test_media_upload_base64_requires_mime() -> None:
    with pytest.raises(ValidationError):
        AgentMediaUpload(data_base64="Zm9v")


def test_publication_normalizes_and_dedupes_tags() -> None:
    publication = CoursePublication(tags=["  Python ", "#python", "data science"])
    assert publication.tags == ["python", "data science"]


def test_publication_rejects_invalid_tag_characters() -> None:
    with pytest.raises(ValidationError):
        CoursePublication(tags=["c++!"])


def test_course_write_enforces_body_size_limit() -> None:
    with pytest.raises(ValidationError):
        AgentCourseWrite(
            title="Курс",
            sections=[
                AgentSectionWrite(
                    title="Раздел",
                    articles=[
                        {
                            "title": "Статья",
                            "body": "x" * 1_000_001,
                            "material": AgentSetWrite(title="Набор"),
                        }
                    ],
                )
            ],
        )
