# API genérica de extração de PDFs

Serviço FastAPI assíncrono que recebe PDFs e entrega texto, Markdown ou JSON versionado. A API
tenta leitura nativa primeiro e aciona OCR somente nas páginas cuja camada de texto é ausente ou
insuficiente. Texto, tabelas, imagens, coordenadas, ordem de leitura e origem permanecem
rastreáveis.

Produção: `https://apiextracaopdf-production.up.railway.app`

- Interface: `/ui/`
- OpenAPI: `/docs`
- Vivacidade: `/health`
- Prontidão: `/ready`

## Responsabilidade e arquitetura

```text
cliente -> FastAPI -> PDF temporário + PostgreSQL (job/eventos com TTL)
                         |
worker -> PyMuPDF nativo por página -> heurística -> EasyOCR/Marker nas páginas necessárias
                         |
              normalização -> texto/Markdown/JSON -> SSE + resultado temporário
                         |
              limpeza por TTL -> remove PDF, imagens, páginas e resultado
```

A extração é genérica e não cria clientes, pedidos ou outros registros de negócio. O módulo legado
de estruturação foi isolado em `app.structure_main`/`app.structure_combined`; ele não é montado nem
executado pelo serviço padrão. As rotas antigas `/api/v1` de documentos foram mantidas por
compatibilidade, mas novas integrações devem usar `/v1/extractions`.

Principais módulos:

- `app/api/v1/extractions.py`: contrato HTTP, status, resultado, SSE e cancelamento.
- `app/services/extraction_service.py`: aplicação, isolamento do job e armazenamento temporário.
- `app/extraction`: validação, PyMuPDF, Marker, roteamento híbrido e normalização.
- `app/workers/extraction_worker.py`: claim concorrente, progresso, resultado e limpeza.
- `app/storage.py`: interface desacoplada e backend local protegido contra path traversal.
- `app/schemas/extraction_api.py`: schema JSON público `1.0`.

PostgreSQL com `FOR UPDATE SKIP LOCKED` permite vários workers. O backend local exige volume
compartilhado e, portanto, limita escalabilidade horizontal da API; S3/MinIO é a evolução prevista
pela interface de storage.

## Instalação e execução

Requer Python 3.12.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
Copy-Item .env.example .env
.\.venv\Scripts\python.exe -m alembic upgrade head
```

Em terminais separados:

```powershell
.\.venv\Scripts\python.exe -m app.main
.\.venv\Scripts\python.exe -m app.workers.extraction_worker
```

`python -m app.combined` inicia API e worker no mesmo processo, como no Railway. Para executar a
estruturação comercial separadamente, use `python -m app.structure_combined` em outro serviço.

## API v1

| Método | Rota | Finalidade |
|---|---|---|
| `POST` | `/v1/extractions` | valida o PDF, cria job temporário e responde `202` |
| `GET` | `/v1/extractions/{job_id}` | status e progresso (fallback ao SSE) |
| `GET` | `/v1/extractions/{job_id}/events` | eventos SSE, heartbeat e reconexão |
| `GET` | `/v1/extractions/{job_id}/result` | texto, Markdown ou JSON solicitado |
| `DELETE` | `/v1/extractions/{job_id}` | cancela e remove arquivos temporários |
| `GET` | `/health` | vivacidade |
| `GET` | `/ready` | banco pronto |

Upload:

```bash
curl -X POST http://localhost:8000/v1/extractions \
  -H 'Idempotency-Key: exemplo-unico' \
  -F 'file=@documento.pdf;type=application/pdf' \
  -F 'output_format=json' \
  -F 'ocr_mode=auto' \
  -F 'ocr_language=por' \
  -F 'extract_images=true' \
  -F 'extract_tables=true' \
  -F 'include_coordinates=true' \
  -F 'image_output=reference' \
  -F 'pages=1,3,5-10' \
  -F 'processing_mode=async'
```

Resposta:

```json
{
  "job_id": "uuid",
  "status": "queued",
  "events_url": "/v1/extractions/uuid/events",
  "status_url": "/v1/extractions/uuid",
  "result_url": "/v1/extractions/uuid/result",
  "expires_at": "ISO-8601"
}
```

SSE com reconexão:

```bash
curl -N http://localhost:8000/v1/extractions/JOB_ID/events
curl -N -H 'Last-Event-ID: 12' http://localhost:8000/v1/extractions/JOB_ID/events
```

Eventos persistem apenas pelo TTL do job e incluem `job.queued`, `job.started`, `ocr.started`,
`page.processed`, `job.completed`, `job.failed` e `job.cancelled`. A conexão envia heartbeat,
encerra no estado terminal e tem timeout configurável.

`output_format` aceita `text`, `markdown` ou `json` (padrão). `ocr_mode` aceita `auto`, `always` e
`never`. `image_output` aceita referência temporária, Base64 ou somente metadados. Referências não
expõem caminhos internos e expiram com o job.

`pages` define as páginas processadas e usa numeração iniciada em 1. O padrão é `all`; também
aceita uma página (`5`), lista (`1,3,8`), intervalo (`10-20`), combinação (`1,3,5-10`), páginas
ímpares (`odd`) ou pares (`even`). Repetições são removidas e o resultado é ordenado. Seletores
malformados, fora do documento ou acima de `MAX_SELECTED_PAGES` retornam `422`. Extração nativa,
OCR, tabelas e imagens executam somente nas páginas selecionadas; o progresso SSE usa esse mesmo
total.

O JSON público contém `schema_version`, metadados do documento, `page_selection`, processamento,
páginas, blocos,
tabelas, imagens e estatísticas. Cada bloco declara `source`: `native`, `ocr`, `image`, `table`,
`metadata` ou `hybrid`. Tabelas incluem cabeçalhos, linhas, colunas, células, Markdown, método e
confiança. Imagens incluem hash, classificação determinística, coordenadas e associação textual.

## OCR automático e normalização

O PyMuPDF sempre inspeciona cada página. EasyOCR é o fallback CPU padrão; ele tenta primeiro a
orientação original e só avalia rotações quando quantidade/confiança forem insuficientes. Marker
permanece como fallback avançado quando sua infraestrutura VLM estiver disponível. OCR é solicitado quando caracteres ou palavras ficam
abaixo do limite ou quando a proporção de caracteres inválidos supera o configurado. O roteador
cria um PDF temporário de uma única página para o Marker e mescla o resultado com imagens/tabelas
nativas, evitando OCR do documento inteiro. Falha isolada vira warning e não descarta as demais
páginas.

A normalização aplica Unicode NFKC, remove controles, corrige hifenização entre linhas, reduz
espaços/quebras, elimina blocos duplicados e identifica cabeçalhos/rodapés repetidos. Alterações
relevantes deixam rastreabilidade em metadados e warnings.

## Configuração

Consulte `.env.example`. Variáveis principais:

- `DATABASE_URL`, `STORAGE_PATH`, `MAX_PDF_SIZE_MB`, `MAX_PDF_PAGES`, `MAX_SELECTED_PAGES`.
- `PDF_NATIVE_MIN_CHARS_PER_PAGE`, `PDF_NATIVE_MIN_WORDS_PER_PAGE`.
- `PDF_NATIVE_MAX_INVALID_CHAR_RATIO`, `OCR_DPI`, `OCR_DEFAULT_LANGUAGE`.
- `OCR_MAX_CONCURRENCY`, `MAX_IMAGES_PER_DOCUMENT`, `IGNORE_REPEATED_IMAGES`.
- `MARKER_FONT_PATH` (cache gravável da fonte auxiliar usada pelo Marker).
- `EASYOCR_MODEL_PATH` (cache gravável dos modelos OCR CPU).
- `EXTRACTION_TIMEOUT_SECONDS`, `EXTRACTION_JOB_TTL_SECONDS`.
- `EXTRACTION_CLEANUP_INTERVAL_SECONDS`, `EXTRACTION_SSE_HEARTBEAT_SECONDS`.
- `EXTRACTION_SSE_TIMEOUT_SECONDS`, `CORS_ALLOWED_ORIGINS`.

Em produção, use PostgreSQL e um volume em `/data`; no perfil local, SQLite suporta apenas um
worker. Não use `*` no CORS com credenciais habilitadas.

## Erros, segurança e privacidade

Erros usam envelope estável com `code`, mensagem segura, detalhes e `request_id`. Há códigos para
arquivo inválido, PDF vazio/corrompido/protegido, limites, job inexistente/expirado/cancelado,
resultado não pronto e falhas de processamento. Tracebacks ficam apenas no banco/log interno.

A validação confere extensão, MIME, assinatura real, tamanho, páginas, criptografia e corrupção.
Nomes são sanitizados, o storage impede path traversal e nenhuma ação incorporada no PDF é
executada. Logs não incluem conteúdo integral, Base64, tokens ou credenciais. Autenticação/API key
e isolamento multiempresa ainda devem ser aplicados no gateway ou em uma evolução do modelo de
cliente; não exponha esta versão diretamente a múltiplos tenants não confiáveis.

## Testes e qualidade

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check app tests
.\.venv\Scripts\python.exe -m mypy app
```

Testes pesados do Marker são opt-in: `pytest -m slow -o addopts=''`. Testes comuns usam PDFs
sintéticos e mocks, sem chamadas externas.

## Railway

`railway.json` aplica `alembic upgrade head`, inicia `python -m app.combined` e verifica `/health`.
Configure `DATABASE_URL=${{Postgres.DATABASE_URL}}`, `APP_ENV=production` e
`STORAGE_PATH=/data/documents`, `EASYOCR_MODEL_PATH=/data/easyocr-models` e
`MARKER_FONT_PATH=/data/marker/GoNotoCurrent-Regular.ttf`; anexe um volume a `/data`. O primeiro OCR pode baixar/carregar
modelos grandes, consumir vários GiB de RAM e ser lento em CPU.

## Limitações conhecidas e próximos passos

- O fallback atual é por página; OCR por região/imagem ainda é evolução futura.
- Classificação/associação de imagens é determinística e conservadora, sem IA generativa.
- PyMuPDF não recupera toda tabela ou ordem semântica de PDFs graficamente complexos.
- Storage local não suporta réplicas independentes; implementar S3/MinIO antes de escalar API e
  workers separadamente.
- Adicionar API keys, `client_id`, rate limiting, quotas e limites de conexões SSE para ambiente
  multiempresa.
- Instrumentar métricas Prometheus/OpenTelemetry; os logs estruturados já carregam IDs e etapas.
