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
        AgentMediaUpload(
            source_url="https://example.com/a.png", data_base64="Zm9v", mime="image/png"
        )


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


@pytest.mark.parametrize(
    "field,limit",
    [
        ("term", 10000),
        ("definition", 10000),
        ("hint", 1000),
        ("term_transcription", 300),
        ("code_language", 50),
    ],
)
def test_card_text_boundaries(field, limit):
    body = {"term": "Вопрос", "definition": "Ответ", field: "x" * limit}
    CardWrite.model_validate(body)
    with pytest.raises(ValidationError):
        CardWrite.model_validate({**body, field: "x" * (limit + 1)})


@pytest.mark.parametrize("field", ["wrong_term_answers", "wrong_definition_answers", "alt_answers"])
def test_answer_array_boundaries(field):
    body = {"term": "Вопрос", "definition": "Ответ", field: [str(i) for i in range(30)]}
    CardWrite.model_validate(body)
    with pytest.raises(ValidationError):
        CardWrite.model_validate({**body, field: [str(i) for i in range(31)]})


@pytest.mark.parametrize("values", [["Еж", " ёж "], ["  "], ["Ёж"]])
def test_distractor_normalization_matches_synonyms(values):
    with pytest.raises(ValidationError):
        CardWrite(
            term="Вопрос", definition="Ответ", alt_answers=["еж"], wrong_definition_answers=values
        )


def test_example_course_is_valid():
    from pathlib import Path

    path = (
        Path(__file__).resolve().parents[2] / "skills/remora-author/references/example-course.json"
    )
    AgentCourseWrite.model_validate_json(path.read_text())


@pytest.mark.parametrize("articles,cards,body_size", [(101, 0, 0), (2, 2501, 0), (11, 0, 100000)])
def test_course_aggregate_limits(articles, cards, body_size):
    article = {
        "title": "Тема",
        "body": "x" * body_size,
        "material": {"title": "Карточки", "cards": [{"term": "A", "definition": "B"}] * cards},
    }
    sections = [{"title": "Раздел", "articles": [article] * min(articles, 100)}]
    if articles > 100:
        sections.append({"title": "Второй", "articles": [article] * (articles - 100)})
    with pytest.raises(ValidationError):
        AgentCourseWrite(title="Курс", sections=sections)


@pytest.mark.parametrize(
    "value", ["pip install <package>", "`pip install <package>`", "List<T>", "<b>текст</b>"]
)
@pytest.mark.parametrize("side", ["term", "definition"])
@pytest.mark.parametrize("content_type", ["text", "latex"])
def test_html_like_placeholders_rejected_before_request(value, side, content_type):
    with pytest.raises(ValidationError, match=side):
        CardWrite.model_validate(
            {"term": "Вопрос", "definition": "Ответ", side: value, "content_type": content_type}
        )


@pytest.mark.parametrize(
    "language,value",
    [
        ("bash", "pip install <package>"),
        ("cpp", "List<T>"),
        ("html", "<b>текст</b>"),
        ("text", "<placeholder>"),
    ],
)
def test_literal_code_preserves_angle_brackets(language, value):
    card = CardWrite(
        term=value, definition="Объяснение", content_type="code", code_language=language
    )
    assert card.term == value


def test_code_requires_language():
    with pytest.raises(ValidationError, match="code_language"):
        CardWrite(term="print(1)", definition="Печать", content_type="code")


def test_plain_math_and_cyrillic_not_rejected():
    card = CardWrite(term="x < 5 и x > 0", definition="0 < x < 5; ёлка — дерево")
    assert card.term == "x < 5 и x > 0"
