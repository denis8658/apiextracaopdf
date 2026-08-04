from typing import Any


class AppError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        status_code: int = 400,
        details: Any = None,
    ) -> None:
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details
        super().__init__(message)


ERROR_MESSAGES = {
    "invalid_file_extension": "Somente arquivos com extensão .pdf são aceitos.",
    "invalid_content_type": "O tipo de conteúdo deve ser application/pdf.",
    "invalid_pdf_signature": "O arquivo enviado não possui uma assinatura PDF válida.",
    "empty_file": "O arquivo enviado está vazio.",
    "file_too_large": "O arquivo excede o limite configurado.",
    "page_limit_exceeded": "O PDF excede o limite de páginas configurado.",
    "corrupted_pdf": "O arquivo PDF está corrompido ou não pôde ser aberto.",
    "pdf_engine_unavailable": "O componente de leitura de PDF não está instalado no servidor.",
    "encrypted_pdf_not_supported": "PDFs protegidos por senha não são suportados.",
    "storage_error": "Não foi possível armazenar o documento.",
    "not_found": "Documento não encontrado.",
    "result_not_ready": "O resultado ainda não está disponível.",
    "idempotency_conflict": "A chave de idempotência já foi usada com outro conteúdo.",
}


def pdf_error(code: str, status_code: int = 400) -> AppError:
    return AppError(code, ERROR_MESSAGES[code], status_code)
