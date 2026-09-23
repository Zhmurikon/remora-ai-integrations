# JSON API contract

Complete valid creation example: [example-course.json](example-course.json).
Pass its object as `course` to MCP `create_course`, with a separate new `request_key`.
It is a structural illustration, not content to upload automatically. Its short distractor
lists are intentional: three is not a schema requirement. Replace the teaching content with
the user's requested material, applying authoring.md before saving.

Base: configured API origin + `/api/v1/agent`. Authenticate using
`Authorization: Bearer <personal token from secret environment>` over HTTPS (HTTP only locally).
Never follow cross-origin redirects with credentials.

| Method | Path                                                   | Required scope  |
| ------ | ------------------------------------------------------ | --------------- |
| GET    | `/sets`, `/courses` (`offset=0&limit=20`, maximum 100) | materials:read  |
| GET    | `/sets/{id}`, `/courses/{id}`                          | materials:read  |
| POST   | `/sets`, `/courses`                                    | materials:write |
| POST   | `/media`                                                | materials:write |
| PUT    | `/sets/{id}`, `/courses/{id}`                          | materials:write |
| GET    | `/courses/{id}/structure`                              | materials:read  |
| PUT    | `/courses/{id}/structure`                              | materials:write |
| POST   | `/courses/{id}/copy`                                   | materials:write |
| POST   | `/courses/{id}/articles/{article_id}/delete`           | materials:write |
| POST   | `/courses/{id}/sections/{section_id}/delete`           | materials:write |
| POST   | `/courses/{id}/publish`, `/courses/{id}/unpublish`     | courses:publish |

All writes require `Idempotency-Key`: new UUID per logical operation, identical retries reuse it.
PUT also requires the latest `revision` from GET. Reusing a key with a different payload is 409.
Errors use `{code, message, details?}`. OpenAPI: `/openapi.json` on the API origin.

Image upload accepts exactly one source:

```json
{"source_url":"https://public.example/diagram.png","alt":"Диаграмма"}
```

or `{"data_base64":"...","mime":"image/png","filename":"diagram.png","alt":"..."}`.
The URL must be public HTTPS; redirects and private/reserved addresses are rejected. The response
contains `id`, validated metadata and `markdown_reference` ready for article body. SVG is sanitized.
Every upload requires `Idempotency-Key` and `materials:write`.

Set input:

```json
{
  "title": "Клетка",
  "description": "Источник: конспект пользователя, глава 1",
  "lang_term": "ru",
  "lang_definition": "ru",
  "cards": [
    {
      "term": "Какая органелла синтезирует белок?",
      "definition": "Рибосома",
      "wrong_definition_answers": ["Лизосома", "Центриоль", "Вакуоль"]
    }
  ]
}
```

Course input: `{title, description?, sections: [{id?, title, articles: [{id?, title, body?, material: SET_INPUT}]}]}`.
Update adds top-level `revision`. Existing card input includes `id`; nested sets do not take
set `id` or `revision`: article ID identifies the linked set and course revision covers all content.
Read response includes IDs, positions, timestamps, visibility, counts and revisions; these are not
write fields. Preserve optional card data such as hint, alt_answers, transcriptions, image IDs,
content_type and code_language when updating.

Limits per course request: 50 sections, 100 articles, 5000 cards, 100,000 characters per body,
1,000,000 characters of theory total. Empty courses/sections are allowed as drafts. No implicit deletion
of sections/articles. Empty card sets may be drafts; every article needs cards before publication.
Publishing takes `{ "tags": ["биология"] }`; unpublishing has no body.

## Structure-only operations (E6B)

MCP `get_course_structure(course_id)` returns sections/articles with `set_id`, theory and a
structure-only `revision`, not complete cards. Use this revision only for structure updates
and deletions; `get_course` provides a different revision covering the full course/cards.

MCP `update_course_structure(course_id, structure, request_key)` / PUT structure body:

```json
{
  "revision": "<from get_course_structure>",
  "sections": [
    {
      "id": "<existing section ID, omit for new>",
      "title": "Основы",
      "articles": [
        {
          "id": "<existing article ID, omit for new>",
          "title": "Матрицы",
          "body": "# Матрицы\n\n**Матрица** — таблица чисел.",
          "set_id": "<existing linked set ID>"
        }
      ]
    }
  ]
}
```

Include all existing sections/articles exactly once. Existing articles retain their set_id.
For a new article, set_id attaches an own set not already linked to any article; omitted/null
set_id creates a new empty set. Cards are not replaced. Empty sections are allowed.
Publication must be removed separately before mutations.

## Copy/delete operations

- `copy_course(course_id, selection, request_key)`: POST copy with `{}` or `{article_id: UUID}`.
  Own or accessible published sources only. Returns new private CourseEditorDetail (structure),
  not full cards. Read `get_course(new_id)` to verify cards and obtain full-course revision.
  An article becomes its own course; media and wrong answers are copied, study progress is not.
- `delete_course_article(course_id, article_id, deletion, request_key)` and
  `delete_course_section(course_id, section_id, deletion, request_key)` use POST delete paths above.
  Body `{"revision": "<structure revision>", "confirm": true}` is mandatory.
  Section deletion removes all contained article texts. Sets/cards/progress are retained.
  Response is the remaining CourseEditorDetail with new structure revision.
- All writes require materials:write and Idempotency-Key; deletion does not require publish
  scope, but cannot silently unpublish a course. Unsupported target returns 404, stale revision
  or published-course mutation returns 409; missing/false confirmation returns 422.
