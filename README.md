# Pipeline de Anotação Emocional — Corpus B2W → Sentient

Anota reviews da B2W com rótulos afetivos (emoção, VAD — valência/arousal/dominância,
intenção, registro etc.) usando o **Claude Code CLI** em modo headless (`claude -p`).

Não usa o SDK. Cada review vira uma chamada ao `claude -p` via `subprocess`, o que é
mais estável para processamento em lote. A saída é um arquivo **JSONL** (uma anotação por linha).

---

## Como funciona (resumo)

1. **Heurística local (puro Python, custo zero)** estima a complexidade emocional do texto
   (ironia, ambivalência, supressão, resignação...).
2. **Roteador de modelo**: textos simples vão para o **Sonnet**; textos complexos
   (score ≥ 0.55) escalam para o **Opus**. Sonnet com confiança muito baixa também escala.
3. **Anotação** via `claude -p`, que deve retornar **somente um JSON** com os rótulos.
4. **Saída JSONL**: cada linha contém a review original (`source_row`) + a anotação + metadados.

---

## Pré-requisitos

| Requisito | Detalhe |
|---|---|
| **Python 3.10+** | Usa sintaxe de tipos `X \| None`. Não precisa de `pip install` — só biblioteca padrão. |
| **Claude Code CLI** | O comando `claude` precisa estar instalado **e autenticado** (veja abaixo). |
| **SDK** | ❌ **Não é necessário.** O script chama o CLI `claude`, não o SDK Python. |
| **Internet** | Necessária — cada anotação é uma chamada ao modelo. |

> Se você tem o Claude Code instalado e consegue rodar `claude` no terminal, já tem o que precisa.

---

## 1. Configuração

Na maioria dos casos **não é preciso configurar nada**: o script detecta o `claude`
automaticamente no seu `PATH`. A ordem de resolução é:

1. variável de ambiente `CLAUDE_BIN`, se definida;
2. `claude` encontrado no `PATH` (autodetecção);
3. um caminho fixo de fallback no script.

Só faça algo se o `claude` **não** estiver no `PATH`. Aí, ou aponte via variável de ambiente:

```bash
export CLAUDE_BIN="$(command -v claude)"
# ex.: /home/voce/.local/bin/claude   (Linux)
#      /opt/homebrew/bin/claude       (macOS)
```

…ou edite o fallback no topo do `b2w_pipeline.py` (constante `CLAUDE_BIN`).

---

## 2. Autenticação (a pegadinha mais comum)

O `claude -p` precisa estar **logado**. Se você usa o Claude Code normalmente no terminal,
provavelmente já está. Para conferir:

```bash
claude -p --output-format text <<< "responda apenas: ok"
# deve imprimir: ok
```

Se aparecer `Not logged in · Please run /login`, autentique uma vez:

```bash
claude          # abre a sessão; rode /login dentro dela
# ou, se você usa chave de API:
export ANTHROPIC_API_KEY=sk-ant-...
```

> ⚠️ **Não use a flag `--bare`** ao chamar o `claude -p` para este pipeline. O modo `--bare`
> pula o carregamento das credenciais de assinatura e faz a chamada falhar com
> `Not logged in`, mesmo que você esteja logado. O script já está configurado **sem** `--bare`.

---

## 3. Uso básico

```bash
python b2w_pipeline.py <entrada.csv> <saida.jsonl> [opções]
```

Exemplos:

```bash
# Teste rápido com 10 linhas
python b2w_pipeline.py reviews.csv corpus.jsonl --limit 10

# Rodar tudo
python b2w_pipeline.py reviews.csv corpus.jsonl

# Mais chamadas em paralelo (padrão é 5)
python b2w_pipeline.py reviews.csv corpus.jsonl --concurrency 8
```

Ao final, o script imprime um resumo:

```
Concluído:       10 anotados, 0 pulados
Opus usado:      0 (0%)
Fila de revisão: 1 (10%)
Saída:           corpus.jsonl
```

---

## 4. Opções (flags)

| Flag | Padrão | O que faz |
|---|---|---|
| `--limit N` | (sem limite) | Processa só as primeiras `N` linhas válidas. Ótimo para testar. |
| `--resume` | desligado | **Continua de onde parou** sem reprocessar o que já foi feito (veja abaixo). |
| `--concurrency N` | `5` | Número de chamadas ao `claude` em paralelo. |

> 📌 **A flag é `--resume`** (continuar/retomar). Não existe `--reload` neste script.

---

## 5. Retomar processamento — `--resume`

Lotes grandes podem ser interrompidos (Ctrl+C, queda de rede, etc.). A saída é gravada
**incrementalmente** (modo *append*, com `flush` a cada linha), então o que já foi feito está salvo.

Para continuar **no mesmo arquivo de saída**, é só repetir o comando com `--resume`:

```bash
# Primeira rodada (interrompida no meio)
python b2w_pipeline.py reviews.csv corpus.jsonl

# Continua de onde parou, sem refazer o que já está em corpus.jsonl
python b2w_pipeline.py reviews.csv corpus.jsonl --resume
```

**Como ele sabe o que já foi feito:** ao retomar, o script lê o `corpus.jsonl` existente e
monta um conjunto de IDs já processados (`reviewer_id` + `product_id`). Linhas com esse ID
são puladas.

Observações importantes:
- Use **o mesmo arquivo de saída** das duas vezes — é nele que o `--resume` procura o progresso.
- **Sem** `--resume`, novas execuções **acrescentam** ao arquivo existente (não sobrescrevem).
  Para começar do zero, apague o arquivo antes: `rm corpus.jsonl`.
- Linhas que deram **erro** na rodada anterior também contam como "já processadas" e
  **não** serão refeitas no `--resume`. Para reprocessá-las, filtre-as do JSONL antes.

### Quando os tokens/uso acabam

Se o limite de uso da sua conta (tokens) estourar no meio do lote, o script **detecta**
a mensagem do `claude`, **interrompe o lote inteiro** e sai com código `2`, imprimindo:

```
⛔ LIMITE DE USO (TOKENS) ATINGIDO — lote interrompido.
   ...
```

As linhas pendentes **não** são gravadas como erro (ao contrário de falhas pontuais),
justamente para que o `--resume` as repegue. Depois que os tokens resetarem, é só
**re-rodar o mesmo comando com `--resume`** que ele continua de onde parou:

```bash
python b2w_pipeline.py reviews.csv corpus.jsonl --resume
```

> Isso vale para exaustão de cota (que reseta por janela). Picos transitórios de
> rate-limit numa única chamada continuam tratados como erro daquela linha, sem parar o lote.

---

## 6. Formato de entrada (CSV)

CSV com cabeçalho. O encoding é detectado automaticamente (`utf-8`, `utf-8-sig`, `latin-1`, `cp1252`).
As colunas usadas pelo pipeline são:

| Coluna | Uso |
|---|---|
| `review_text` | **Obrigatória.** Texto da review. Linhas com menos de 15 caracteres ou menos de 4 palavras são puladas. |
| `review_title` | Título da review (contexto). |
| `overall_rating` | Nota 1–5, usada como âncora de valência. |
| `site_category_lv1` | Categoria do produto (contexto). |
| `reviewer_id`, `product_id` | Usados como chave de deduplicação no `--resume`. |

Outras colunas do dataset B2W são preservadas em `source_row`, mas não influenciam a anotação.

---

## 7. Formato de saída (JSONL)

Uma linha JSON por review. Em caso de sucesso:

```json
{
  "source_row":       { "...": "linha original do CSV" },
  "model":            "claude-sonnet-4-6",
  "escalated":        false,
  "complexity_score": 0.0,
  "signals":          [],
  "rating_anchor":    4,
  "annotation": {
    "emocao_primaria":   "satisfação",
    "emocao_secundaria": "surpresa",
    "valenca":     4.5,
    "arousal":     3.0,
    "dominancia":  4.5,
    "intensidade": 0.8,
    "emocao_mista": false,
    "intencao":  "elogiar",
    "registro":  "informal",
    "confianca": 0.92,
    "raciocinio": "..."
  },
  "needs_review":   false,
  "annotated_at":   "2026-06-16T19:30:00+00:00",
  "prompt_version": "b2w-v1.1"
}
```

`needs_review: true` marca anotações com confiança < 0.80, para revisão humana posterior.

Em caso de falha numa linha específica, em vez de `annotation` a linha terá um campo
`error` com a mensagem, e `needs_review: true` — o lote continua sem parar.

---

## 8. Solução de problemas

| Sintoma | Causa provável | Solução |
|---|---|---|
| Todas as linhas dão `Erros=N` | `claude` não autenticado, ou não encontrado | Confira o login (passo 2) e se `command -v claude` acha o binário (passo 1). |
| `claude -p falhou ... Not logged in` | Sessão não autenticada / uso de `--bare` | Rode `claude` e `/login`; garanta que **não** há `--bare` na chamada. |
| `FileNotFoundError: .../claude` | `claude` não está no `PATH` | `export CLAUDE_BIN="$(command -v claude)"` ou ajuste o fallback no script (passo 1). |
| `SyntaxError` ao iniciar | Python < 3.10 | Use Python 3.10 ou superior. |
| `json.decoder.JSONDecodeError` numa linha | O modelo devolveu algo fora do JSON | Linha vai para `error`; o lote segue. Reexecute com `--resume`. |
| `⛔ LIMITE DE USO (TOKENS) ATINGIDO` (saída com código 2) | Cota de uso/tokens da conta esgotada | Espere o reset e **re-rode o mesmo comando com `--resume`**. As pendentes não viram erro, são repegues automaticamente. |

---

## Modelos usados

Definidos no topo do `b2w_pipeline.py`:

```python
SONNET = "claude-sonnet-4-6"
OPUS   = "claude-opus-4-8"
```

Mantenha esses IDs atualizados para os modelos Claude mais recentes disponíveis na sua conta.
