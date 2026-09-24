"""Самостоятельные копии контракта агентского API Remora.

Валидация здесь — только ради быстрой обратной связи агенту без похода в сеть.
Финальную проверку всегда делает сервер: любые расхождения проявятся как 422,
контракт описан в ../../skills/remora-author/references/api.md.
"""

import re
from enum import Enum
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    StringConstraints,
    field_validator,
    model_validator,
)


class ContentType(str, Enum):
    text = "text"
    latex = "latex"
    code = "code"


def _normalize_option(value: str) -> str:
    # Должно совпадать с app.core.distractors.normalize_option в API.
    return " ".join(value.strip().lower().replace("ё", "е").split())


class CardWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID | None = None
    term: str = Field(max_length=10_000)
    definition: str = Field(max_length=10_000)
    term_transcription: str | None = Field(default=None, max_length=300)
    definition_transcription: str | None = Field(default=None, max_length=300)
    hint: str | None = Field(default=None, max_length=1000)
    content_type: ContentType = ContentType.text
    code_language: str | None = Field(default=None, max_length=50)
    term_image_id: UUID | None = None
    definition_image_id: UUID | None = None
    alt_answers: list[str] = Field(default_factory=list, max_length=30)
    wrong_term_answers: list[Annotated[str, Field(max_length=10_000)]] = Field(
        default_factory=list, max_length=30
    )
    wrong_definition_answers: list[Annotated[str, Field(max_length=10_000)]] = Field(
        default_factory=list, max_length=30
    )

    @model_validator(mode="after")
    def validate_content_format(self) -> Self:
        # Повторяем ограничение ContentService, чтобы агент не искал ошибку в целом курсе.
        if self.content_type == ContentType.code:
            if not self.code_language:
                raise ValueError(
                    "Для content_type=code нужен code_language, например bash или text"
                )
        else:
            for field in ("term", "definition"):
                if re.search(r"<\s*/?\s*[a-zA-Z][^>]*>", getattr(self, field)):
                    raise ValueError(
                        f"{field}: API не принимает HTML-подобные фрагменты, включая <package>, "
                        "при content_type=text/latex. Для программного примера используйте "
                        "content_type=code и code_language (например bash). "
                        "Обратные кавычки не отменяют проверку. Не меняйте смысл примера."
                    )
        return self

    @model_validator(mode="after")
    def validate_wrong_answers(self) -> Self:
        for field, correct in (
            ("wrong_term_answers", self.term),
            ("wrong_definition_answers", self.definition),
        ):
            seen = {_normalize_option(correct), *(_normalize_option(a) for a in self.alt_answers)}
            values = getattr(self, field)
            for value in values:
                key = _normalize_option(value)
                if not key or key in seen:
                    raise ValueError(
                        "Неверные ответы не должны быть пустыми, повторяться "
                        "или совпадать с правильным ответом и его синонимами"
                    )
                seen.add(key)
            setattr(self, field, [value.strip() for value in values])
        return self


class CourseMetadata(BaseModel):
    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=160)]
    description: str = Field(default="", max_length=5000)


class AgentSetWrite(CourseMetadata):
    model_config = ConfigDict(extra="forbid")
    lang_term: str = Field(default="ru", min_length=2, max_length=10)
    lang_definition: str = Field(default="ru", min_length=2, max_length=10)
    cards: list[CardWrite] = Field(default_factory=list, max_length=5000)


class AgentSetUpdate(AgentSetWrite):
    revision: str = Field(min_length=64, max_length=64)


class AgentArticleWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID | None = None
    title: str = Field(min_length=1, max_length=160)
    body: str = Field(default="", max_length=100_000)
    material: AgentSetWrite


class AgentSectionWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID | None = None
    title: str = Field(min_length=1, max_length=160)
    articles: list[AgentArticleWrite] = Field(default_factory=list, max_length=100)


class AgentCourseWrite(CourseMetadata):
    model_config = ConfigDict(extra="forbid")
    sections: list[AgentSectionWrite] = Field(default_factory=list, max_length=50)

    @model_validator(mode="after")
    def check_size(self) -> Self:
        articles = [a for s in self.sections for a in s.articles]
        if len(articles) > 100 or sum(len(a.material.cards) for a in articles) > 5000:
            raise ValueError("Один запрос: до 100 статей и 5000 карточек")
        if sum(len(a.body) for a in articles) > 1_000_000:
            raise ValueError("Один запрос: до 1 000 000 символов теории")
        return self


class AgentCourseUpdate(AgentCourseWrite):
    revision: str = Field(min_length=64, max_length=64)


class AgentStructureDelete(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: str = Field(min_length=64, max_length=64)
    confirm: Literal[True]


class AgentMediaUpload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_url: HttpUrl | None = None
    data_base64: str | None = Field(default=None, max_length=14_000_000)
    filename: str | None = Field(default=None, min_length=1, max_length=255)
    mime: str | None = Field(default=None, max_length=100)
    alt: str = Field(default="Изображение", max_length=500)

    @model_validator(mode="after")
    def exactly_one_source(self) -> Self:
        if (self.source_url is None) == (self.data_base64 is None):
            raise ValueError("Укажите ровно один источник: source_url или data_base64")
        if self.data_base64 is not None and not self.mime:
            raise ValueError("Для data_base64 укажите mime")
        return self


class CourseCopyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    article_id: UUID | None = None


class CoursePublication(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tags: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("tags")
    @classmethod
    def normalize_tags(cls, values: list[str]) -> list[str]:
        result: list[str] = []
        for value in values:
            tag = " ".join(value.strip().lstrip("#").lower().split())
            if not tag or len(tag) > 60 or any(not (c.isalnum() or c in " -_") for c in tag):
                raise ValueError("Тег должен содержать 1–60 букв, цифр, пробелов или дефисов")
            if tag not in result:
                result.append(tag)
        return result


class SectionStructureWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID | None = None
    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=160)]
    articles: list["ArticleStructureWrite"] = Field(default_factory=list, max_length=100)


class ArticleStructureWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID | None = None
    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=160)]
    body: str = Field(default="", max_length=100_000)
    set_id: UUID | None = None


class CourseStructureWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: str
    sections: list[SectionStructureWrite] = Field(max_length=50)

    @model_validator(mode="after")
    def limits(self) -> Self:
        articles = [a for s in self.sections for a in s.articles]
        if len(articles) > 100 or sum(len(a.body) for a in articles) > 1_000_000:
            raise ValueError("До 100 статей и 1 000 000 символов теории в курсе")
        return self
