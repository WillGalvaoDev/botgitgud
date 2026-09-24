# Revisão independente Astra — M2.1

Status: **MILESTONE_CLOSED**. Data: 2026-09-22.

Fechamento local de M2.1, após inspeção da implementação, dos testes e das
evidências diretas. Não fecha o macro M2, não inicia M2.2 e não retoma o
orquestrador. Nenhuma correção de implementação foi feita pelo revisor.

## Autoridade e versão examinadas

- `docs/m0-methodology-contract.md`: PRODUCT_CONTRACT, particularmente C06/C07/C10.
- `docs/milestone-workflow.md`: AGENT_WORKFLOW, incluindo a regra de que um
  achado bloqueia somente por violação de AC ou invariante necessária.
- `docs/methodology-roadmap-m2-m6.md`: ROADMAP e fronteiras M2.1/M2.2/M2.3.
- `docs/m1-specification.md` e `docs/m1-closure.md`: a declaração posterior de
  fechamento de M1 prevalece sobre o cabeçalho histórico da SPEC.
- SPEC aceita imutável `docs/submilestones/M2.1/spec-v001.md` e `.json`, spec_sha
  `721dbbaaa725962ef874ebe6b2e686edaa63ee2439c4853619e4115cd1e8b77a`.
- Produto examinado em `C:/Users/wsgon/OneDrive/Desktop/BotGITGUD-M2.1-product`:
  checkpoint attempt-00012, commit `affd8653390cd718f28c30ebf0c73060efe8511c`,
  identificador de árvore registrado
  `733b72ae730e9911a663d56ba165cdb07ddb7ec8f5fcf758d4f6ff3b7560b21a`.
  `git status --short` e `git diff --stat` estavam vazios na revisão final.
  Proveniência da seleção em `provenance.json`; attempt-00013 não acrescentou
  delta material, conforme esse registro.

## Resultado por critério

| Critério | Avaliação independente |
|---|---|
| AC1 | Cinco eixos sempre executados, precedência INELIGIBLE > INDETERMINATE > ELIGIBLE, versão fixa e razões ordenadas por eixo e alfabeticamente dentro dele. Os campos lidos pertencem às fontes autorizadas. |
| AC2 | Partição desconhecida abstém, diferença de partição exclui; referência wipe exclui, alvo wipe declara limitação; hotfix não observável é explicitado no eixo e na população elegível. |
| AC3 | Função individual recebe somente dois logs; agregação preserva resultados e ordem de entrada, sem reparo por escassez. Piso 8 importado, usado apenas para declarar insuficiência. |
| AC4 | None de partição preservado, valores de classe/spec observados mantêm espaços e caixa, e nenhuma equivalência temporal deriva de timestamp. |
| AC5 | Módulo local sem wiring, covariáveis, consultas, migração ou redefinição de medidas M1. Elegibilidade básica não substitui reconciliação quantitativa. |
| AC6 | Tabela normativa de casos e resultados precede a implementação; testes, replay real da fixture, verificações estáticas e suíte ampla possuem evidência direta. Censo adicional por motivo e hashes estão abaixo. |

`spec-before-implementation.json` ancora a tabela normativa da SPEC em SPEC_READY
(audit 15, 2026-09-15T12:39:59.416378+00:00), anterior ao ATTEMPT_INPUT do Sonnet
(audit 17, 12:40:24.261349+00:00). A cópia publicada é byte-idêntica à SPEC aceita.
A cronologia comprovada é a da tabela normativa; as tabelas da entrega relacionam
esses casos aos testes, sem substituir as regras por resultados da implementação.

## Evidência executada, distinta da previsão documental

O documento de autoria `docs/m2-1-review-evidence.md` declara corretamente que suas
colunas inicialmente eram resultados derivados manualmente. O fechamento usa
também os resultados efetivamente executados abaixo, e não trata previsão como
execução:

- `validation/m2-1.json`: **39 passed, 1 skipped**. O skip é o corpus Parquet
  ausente no workspace; o replay da fixture versionada executou e passou.
- `validation/ruff-final.json`: `ruff check .`, exit 0.
- `validation/format-final.json`: `ruff format --check .`, exit 0, 329 arquivos.
- `validation/pyright-final.json`: `pyright`, exit 0, zero erros e avisos.
- `validation/full.json` e `validation/full.xml`: **2610 passed, 1 failed,
  46 skipped, 1 deselected**, 337 warnings, 436,66 segundos reportados pelo pytest.
  Os comandos exatos e saídas estão nos JSONs. Não se declara suíte integral verde.

A única falha ampla é
`tests/unit/test_logging_setup.py::test_no_raw_print_calls_anywhere_in_src`;
o único offender é `src/botgitgud/orchestrator/__main__.py`. É dívida de baseline
preexistente, explicitamente documentada antes desta revisão, fora do write_paths
e sem relação com a política M2.1. O teste permaneceu ativo e a falha foi exposta.
Não constitui regressão introduzida pela unidade nem autoriza expansão do escopo.

## Replay independente e procura de contraexemplos

O revisor executou um probe adicional offline, com Python do venv original,
`-B`, PYTHONPATH apontando explicitamente para src e helpers de testes do produto,
e credenciais CI públicas de placeholder. Nenhuma API, segredo ou arquivo .env
foi necessário. Foram usados `_load_gate1_rankings_logs`, `evaluate_references`,
`snapshot_directory` e `diff_snapshots`, sem modificar a implementação.

Alvo Braska, 19 outras personagens da fixture
`tests/fixtures/gate1_scope/phase1_rankings.json`:

| Medida | Observado |
|---|---:|
| ELIGIBLE | 1 |
| INELIGIBLE | 18 |
| INDETERMINATE | 0 |
| CLASS_MISMATCH | 18 |
| SPEC_MISMATCH | 18 |
| HOTFIX_NOT_OBSERVABLE | 19 |

Contagens de motivos abrangem todos os resultados individuais, portanto hotfix
aparece também nas referências excluídas. Única elegível:
`PhNt3RFYW2dcf8vD:38:Rohanlock`. Limitações: HOTFIX_COMPATIBILITY_UNVERIFIED e
INSUFFICIENT_ELIGIBLE_REFERENCES.

Os 18 arquivos do diretório de fixtures produziram snapshots antes/depois iguais;
`diff_snapshots == []`. Hash de ambos:
`c9716bc32c4537779da508d19ba30097440996c9c7260e59c50aff46613bc739`.
Receita exata do hash: SHA256 dos bytes de
`json.dumps(dataclasses.asdict(snapshot), sort_keys=True).encode()`; o snapshot
contém existência e mapa de caminho relativo para tamanho e SHA256 por arquivo.
Esse censo complementa a evidência de autoria que não discriminava os motivos.

Probe composto reproduzível usando os helpers da suíte:

```python
r = _make_log(class_name="", spec_name="Frost", kill=False,
              duration_s=float("-inf"), partition=None)
result = evaluate_reference(_target(), r)
```

Resultado confirmado: INELIGIBLE, cinco eixos preservados e razões, nesta ordem:
IDENTITY_UNKNOWN, SPEC_MISMATCH, ATTEMPT_STATE_NOT_KILL, INVALID_DURATION,
PARTITION_UNKNOWN, HOTFIX_NOT_OBSERVABLE. Isso verifica simultaneamente ausência
de identidade, incompatibilidade comprovada, duas violações de tentativa e
abstenção temporal, sem esconder eixos após a primeira exclusão.

Não foi encontrado contraexemplo bloqueante aos critérios contratados. Não foram
exigidas garantias sobre tipos inválidos fora do domínio ou consumidores que só
serão integrados em M2.3.

## Limitações preservadas

O replay real cobre uma luta e não substitui o corpus Parquet ausente. Os skips
de corpus não demonstram regressão nem comprovam cobertura ausente. Compatibilidade
de hotfix segue não verificada por decisão da SPEC; covariáveis e seleção são M2.2;
propagação, persistência e efeito na população de produção são M2.3. M3–M6,
treinamento, campanhas e gates experimentais permanecem fora desta entrega.

Nenhuma decisão metodológica adicional ou autorização humana é necessária para
este fechamento local. O orquestrador permanece suspenso e seu histórico intacto.
