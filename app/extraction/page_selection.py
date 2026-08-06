import re

from app.core.errors import AppError

TOKEN_PATTERN = re.compile(r"^[0-9]+(?:-[0-9]+)?$")


def parse_page_selector(selector: str, total_pages: int, limit: int) -> list[int]:
    normalized = selector.strip().lower()
    if not normalized:
        raise AppError("INVALID_PAGE_SELECTOR", "O seletor de páginas está vazio.", 422)
    if normalized == "all":
        selected = list(range(1, total_pages + 1))
    elif normalized == "odd":
        selected = list(range(1, total_pages + 1, 2))
    elif normalized == "even":
        selected = list(range(2, total_pages + 1, 2))
    else:
        selected_set: set[int] = set()
        tokens = normalized.split(",")
        if any(not token or token != token.strip() for token in tokens):
            raise AppError("INVALID_PAGE_SELECTOR", "Seletor de páginas malformado.", 422)
        for token in tokens:
            if not TOKEN_PATTERN.fullmatch(token):
                raise AppError("INVALID_PAGE_SELECTOR", "Seletor de páginas malformado.", 422)
            if "-" in token:
                start_text, end_text = token.split("-", 1)
                start, end = int(start_text), int(end_text)
                if start < 1 or end < 1 or start > end:
                    raise AppError("INVALID_PAGE_RANGE", "Intervalo de páginas inválido.", 422)
                selected_set.update(range(start, end + 1))
            else:
                page = int(token)
                if page < 1:
                    raise AppError("INVALID_PAGE_SELECTOR", "As páginas começam em 1.", 422)
                selected_set.add(page)
        selected = sorted(selected_set)
    if any(page > total_pages for page in selected):
        raise AppError(
            "PAGE_OUT_OF_RANGE",
            f"O seletor contém página maior que o total do documento ({total_pages}).",
            422,
        )
    if len(selected) > limit:
        raise AppError(
            "PAGE_SELECTION_LIMIT_EXCEEDED",
            f"A seleção excede o limite configurado de {limit} páginas.",
            422,
        )
    return selected
