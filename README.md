# API de Extração Documental de PDFs

API FastAPI assíncrona para receber PDFs, persistir metadados no PostgreSQL e extrair texto,
Markdown e JSON por página. O upload responde `202`; um worker separado captura trabalhos com
`FOR UPDATE SKIP LOCKED`, evitando que duas instâncias processem o mesmo item.

O MVP representa fielmente o documento. Ele não interpreta pedidos, não cria planos de corte e
não inventa campos ausentes.

## Arquitetura e fluxo

```text
Base44 -> API -> armazenamento local/volume
             -> PostgreSQL (documento + job queued)
Worker -> PostgreSQL (claim exclusivo) -> PyMuPDF ou Marker -> páginas + resultado
Base44 -> polling de status -> resultado
```

- `app/api`: rotas HTTP, saúde e dependências.
- `app/services`: regras de upload, idempotência, consulta e exclusão.
- `app/extraction`: validação, normalização, motores e roteamento.
- `app/db`: SQLAlchemy 2 assíncrono e modelos.
- `app/workers`: worker persistente baseado no PostgreSQL.
- `alembic`: migração inicial, tabelas, relacionamentos e índices.

O modo `auto` primeiro usa PyMuPDF. Uma página é considerada textualmente suficiente quando tem
ao menos `PDF_NATIVE_MIN_CHARS_PER_PAGE`; o documento usa o motor nativo quando a razão dessas
páginas alcança `PDF_NATIVE_MIN_TEXT_PAGE_RATIO`. A decisão e sua razão ficam registradas no job.
O PDF de teste `5931701f3_Oramento-1790-SEMPREO.pdf` tem 3 páginas, texto nativo e imagens técnicas.
Com os limites padrão, as contagens são 2.983, 3.156 e 666 caracteres e o modo `auto` seleciona
corretamente o motor nativo.

## Instalação local

Requer Python 3.12. PostgreSQL é obrigatório para produção e para concorrência entre workers.

### Teste local imediato no Windows, sem PostgreSQL

O arquivo `.env` local (ignorado pelo Git) usa SQLite e permite testar com **um único worker**.
Não use esse perfil com múltiplos workers: SQLite não oferece a semântica PostgreSQL de
`FOR UPDATE SKIP LOCKED`.

Abra três terminais no diretório do projeto. No primeiro, aplique a migração:

```powershell
.\.venv\Scripts\python.exe -m alembic upgrade head
```

No segundo, mantenha a API aberta:

```powershell
.\.venv\Scripts\python.exe -m app.main
```

No terceiro, mantenha o worker aberto:

```powershell
.\.venv\Scripts\python.exe -m app.workers.extraction_worker
```

Não execute API e worker sequencialmente no mesmo terminal: o processo da API permanece em
execução até ser interrompido. O Python 3.13 global não é a versão homologada. Um `.venv` Python
3.12 já foi criado neste diretório; os exemplos chamam seu executável diretamente.
Os entrypoints também detectam esse ambiente: `python -m app.main` e
`python -m app.workers.extraction_worker` reiniciam automaticamente com o `.venv` 3.12. Se ele
não existir, a inicialização informa claramente a versão exigida.

### Ambiente PostgreSQL

```bash
python -m venv .venv
.venv/Scripts/activate
pip install -e ".[dev]"
copy .env.example .env
alembic upgrade head
python -m app.main
```

Em outro terminal:

```bash
.venv/Scripts/activate
python -m app.workers.extraction_worker
```

Swagger: `http://localhost:8000/docs`. Para desenvolvimento, inicie API e worker separadamente;
ambos devem apontar para o mesmo PostgreSQL e `STORAGE_PATH`.

## Interface de teste

Com a API e o worker em execução, abra:

```text
http://localhost:8000/ui/
```

A rota `/` redireciona para essa interface. Ela permite selecionar ou arrastar um PDF, escolher o
motor, acompanhar o polling e visualizar/copiar/baixar texto, Markdown ou JSON. Por padrão, a tela
usa a mesma origem da API; o campo **API** permite testar outra URL, como a implantação Railway.
Ao usar uma origem diferente, inclua a origem da interface em `CORS_ALLOWED_ORIGINS`.

## Configuração

Todas as opções estão em `.env.example`. Obrigatórias em produção: `DATABASE_URL`, um
`STORAGE_PATH` persistente e o domínio exato do Base44 em `CORS_ALLOWED_ORIGINS`. Limites padrão:
50 MiB, 500 páginas, chunks de 1 MiB e 3 tentativas. `DELETE_PHYSICAL_FILE=false` mantém o arquivo
após exclusão lógica; altere conscientemente.

Não use `*` em origens quando `CORS_ALLOW_CREDENTIALS=true`. Listas usam vírgulas. Para adicionar
o Base44:

```env
CORS_ALLOWED_ORIGINS=https://seu-app.base44.app,http://localhost:5173
```

## Banco, migrações e concorrência

```bash
alembic upgrade head
alembic downgrade base
```

As tabelas são `documents`, `extraction_jobs`, `document_results` e `document_pages`. Resultados
grandes ficam fora de `documents`; JSON usa JSONB no PostgreSQL. O worker incrementa tentativas,
registra duração/erros internos e recoloca o job em `queued` até `EXTRACTION_MAX_ATTEMPTS`.
`error_details_internal` nunca aparece nas respostas públicas.

## Contrato da API

Autenticação não é implementada no MVP. Um gateway futuro pode usar `Authorization` ou
`X-API-Key`, ambos já permitidos pelo CORS. Envie opcionalmente `X-Request-ID`; a API o ecoa ou
gera um UUID. Erros têm a forma:

```json
{"error":{"code":"invalid_pdf_signature","message":"...","details":null,"request_id":"uuid"}}
```

| Objetivo | Método e rota | Entrada | Sucesso | Erros principais |
|---|---|---|---|---|
| Saúde | `GET /health` | - | `200` | - |
| Prontidão | `GET /health/ready` | - | `200` | `503`/`500` se banco indisponível |
| Upload | `POST /api/v1/documents` | multipart: `file`; opcionais `engine`, `output_formats`, `retain_original`; header `Idempotency-Key` | `202` IDs e URLs | `400`, `409`, `413`, `415`, `422`, `500` |
| Documento/status | `GET /api/v1/documents/{id}` | UUID | `200` | `404` |
| Job | `GET /api/v1/extraction-jobs/{id}` | UUID | `200` | `404` |
| Resultado | `GET /api/v1/documents/{id}/result?format=json` | `text`, `markdown` ou `json` | `200` | `404`, `409` |
| Páginas | `GET /api/v1/documents/{id}/pages?page=1&page_size=20` | paginação | `200` | `404`, `422` |
| Página | `GET /api/v1/documents/{id}/pages/{number}` | número iniciado em 1 | `200` | `404` |
| Lista | `GET /api/v1/documents` | `status`, `filename`, datas e paginação | `200` | `422` |
| Reprocessar | `POST /api/v1/documents/{id}/reprocess` | JSON com engine/formatos | `202` | `404`, `409`, `422` |
| Excluir | `DELETE /api/v1/documents/{id}` | UUID | `204` | `404` |

Estados: `queued`, `processing`, `completed`, `failed`, `cancelled`. Não solicite resultado antes
de `completed`; `409 result_not_ready` é esperado enquanto processa.

### Upload para Base44

```javascript
const formData = new FormData();
formData.append("file", selectedFile);
formData.append("engine", "auto");
formData.append("output_formats", "text,markdown,json");
formData.append("retain_original", "true");

const response = await fetch(`${API_BASE_URL}/api/v1/documents`, {
  method: "POST",
  headers: {
    "X-Request-ID": crypto.randomUUID(),
    "Idempotency-Key": crypto.randomUUID()
  },
  body: formData
});
const payload = await response.json();
if (!response.ok) throw new Error(`${payload.error.code}: ${payload.error.request_id}`);
```

Não defina `Content-Type` manualmente com `FormData`; o navegador cria o boundary. Guarde
`document_id` e `job_id`. Repetir a mesma chave e o mesmo conteúdo devolve os IDs originais;
mesma chave com conteúdo diferente retorna `409`.

```bash
curl -X POST http://localhost:8000/api/v1/documents \
  -H "Idempotency-Key: exemplo-1790" \
  -F "file=@pedido.pdf;type=application/pdf" \
  -F "engine=auto" -F "output_formats=text,markdown,json" \
  -F "retain_original=true"
```

### Polling e resultado

```javascript
async function waitForDocument(documentId) {
  for (;;) {
    const response = await fetch(`${API_BASE_URL}/api/v1/documents/${documentId}`);
    const document = await response.json();
    if (!response.ok) throw new Error(document.error.code);
    if (document.status === "completed") return document;
    if (["failed", "cancelled"].includes(document.status)) {
      throw new Error(`Extração encerrada: ${document.status}`);
    }
    await new Promise(resolve => setTimeout(resolve, 2000));
  }
}

await waitForDocument(payload.document_id);
const result = await fetch(
  `${API_BASE_URL}/api/v1/documents/${payload.document_id}/result?format=json`
).then(r => r.json());
```

Chamadas equivalentes:

```bash
curl http://localhost:8000/api/v1/documents/DOCUMENT_ID
curl http://localhost:8000/api/v1/extraction-jobs/JOB_ID
curl 'http://localhost:8000/api/v1/documents/DOCUMENT_ID/result?format=markdown'
curl 'http://localhost:8000/api/v1/documents/DOCUMENT_ID/pages?page=1&page_size=20'
curl http://localhost:8000/api/v1/documents/DOCUMENT_ID/pages/1
curl 'http://localhost:8000/api/v1/documents?status=completed&page=1&page_size=20'
curl -X POST http://localhost:8000/api/v1/documents/DOCUMENT_ID/reprocess \
  -H 'Content-Type: application/json' -d '{"engine":"marker","output_formats":["json"]}'
curl -X DELETE http://localhost:8000/api/v1/documents/DOCUMENT_ID
```

Exemplos JavaScript compactos para os demais endpoints (sempre valide `response.ok` e, em erro,
leia o envelope padronizado):

```javascript
await fetch(`${API_BASE_URL}/health`); // vivacidade
await fetch(`${API_BASE_URL}/health/ready`); // banco pronto
await fetch(`${API_BASE_URL}/api/v1/documents/${documentId}`); // status
await fetch(`${API_BASE_URL}/api/v1/extraction-jobs/${jobId}`); // job
await fetch(`${API_BASE_URL}/api/v1/documents/${documentId}/result?format=text`);
await fetch(`${API_BASE_URL}/api/v1/documents/${documentId}/pages?page=1&page_size=20`);
await fetch(`${API_BASE_URL}/api/v1/documents/${documentId}/pages/1`);
await fetch(`${API_BASE_URL}/api/v1/documents?status=completed&page=1&page_size=20`);
await fetch(`${API_BASE_URL}/api/v1/documents/${documentId}/reprocess`, {
  method: "POST",
  headers: {"Content-Type": "application/json"},
  body: JSON.stringify({engine: "native", output_formats: ["text", "json"]})
});
await fetch(`${API_BASE_URL}/api/v1/documents/${documentId}`, {method: "DELETE"});
```

Ordem obrigatória para o agente Base44: (1) enviar arquivo, (2) armazenar IDs, (3) consultar
status, (4) esperar `completed`, (5) buscar resultado, (6) exibir falhas por `error.code` e
`error.request_id`. Pare o polling ao concluir/falhar e não consulte agressivamente.

### Preflight e troubleshooting CORS

```bash
curl -i -X OPTIONS http://localhost:8000/api/v1/documents \
  -H 'Origin: https://seu-app.base44.app' \
  -H 'Access-Control-Request-Method: POST' \
  -H 'Access-Control-Request-Headers: Authorization,Content-Type,Idempotency-Key'
```

Se faltar `Access-Control-Allow-Origin`, confira protocolo, domínio e porta exatos, reinicie o
serviço após mudar a variável e confirme que não há barra final. Respostas 400/422/500 também
passam pelo middleware CORS. Em upload, remova qualquer `Content-Type` definido pelo frontend.

## Railway

1. Crie PostgreSQL e dois serviços a partir deste repositório: API e Worker.
2. Compartilhe as mesmas variáveis e o mesmo volume montado em `/data`.
3. Defina `STORAGE_PATH=/data/documents`. Sem volume, o disco é efêmero e não serve para retenção.
4. API: use o comando do `railway.json`; ele liga em `0.0.0.0:$PORT` e verifica `/health`.
5. Worker: sobrescreva Start Command com `python -m app.workers.extraction_worker`.
6. Configure Pre-deploy Command somente uma vez/serviço coordenado: `alembic upgrade head`.
   Não rode migrations simultaneamente em cada réplica.
7. Para produção durável, substitua futuramente o backend local por S3/R2.

O Dockerfile usa Python 3.12 slim e usuário não root. Logs JSON vão para stdout e carregam IDs,
sem conteúdo integral do PDF ou segredos.

## Marker: recursos, inicialização e licença

`marker-pdf==2.0.0` está fixado. O primeiro uso pode baixar modelos grandes e aumentar muito o
tempo de startup; CPU é suportada, mas PDFs complexos podem exigir vários GiB de RAM e sofrer
timeout. Separe API/worker, mantenha cache/volume para modelos, limite concorrência e considere GPU
no futuro. Os testes normais usam engine fake/nativa; execute integrações pesadas com
`pytest -m slow -o addopts=''`.

O código da tag está sob Apache-2.0; os pesos usam OpenRAIL-M modificada e têm restrições
comerciais. Leia `THIRD_PARTY_NOTICES.md` e obtenha validação jurídica/licença comercial antes de
produção. A API continua funcional em modo `native` sem carregar modelos Marker.

## Testes e qualidade

```bash
pytest
pytest -m slow -o addopts=''
ruff check .
ruff format --check .
mypy app
```

A suíte cobre upload válido/inválido, assinatura, idempotência, CORS/preflight, OpenAPI, Unicode,
ordem de páginas, seleção nativa, claim do worker e o PDF-modelo local quando disponível.

## Limitações e evolução

- `LocalStorageBackend` requer volume persistente no Railway; S3/R2 ainda não foi implementado.
- O progresso é atualizado no começo/fim; progresso fino por página depende de hooks do Marker.
- O motor nativo preserva blocos/coordenadas, mas não reconstrói semanticamente itens do orçamento.
- Marker não é executado na suíte padrão por custo de download/CPU/RAM.
- Autenticação, multiempresa, agente de IA, esquemas de pedido, plano de corte e filas Redis/Celery
  são evoluções futuras deliberadamente fora do MVP.
