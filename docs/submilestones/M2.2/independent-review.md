# M2.2 — revisão independente — 2026-09-22

Veredito: **REQUIRES_CHANGES**. Routing: Sonnet para implementação/testes/evidências; eventual decisão normativa sobre colisões de identidade volta ao Opus. Não há decisão humana necessária identificada. Nenhuma correção implementada; M2.3 não iniciada.

## Objeto e autoridades

Workspace: `C:/Users/wsgon/OneDrive/Desktop/BotGITGUD-M2.1-product`, HEAD `edba6e62d144811724efe3a2f9bdf0d6023c64ee`, entrega M2.2 ainda não commitada.

Autoridades: M0 C01–C10, SPEC M1, SPEC M2.1 fechada, roadmap e milestone-workflow. A localização da linha de produto foi confirmada pelo pacote de fechamento M2.1 e pela SPEC M2.2.

- SPEC revisada: `docs/m2-2-specification.md`, SHA-256 `ea35eb66efe593076a1fe8efed9b337731f0a970097877ca72fe33ab19d84968`. Igual à cópia `spec-v001.md` e ao campo `spec` de `spec-v001.json`.
- Implementação: `src/botgitgud/analysis/metric_population.py`, SHA-256 `21ce93b2112cbc3097d2f0a3bf36bb16882ca144b8ea3909f1bb8d6382eabdad`.
- Testes: `tests/unit/test_m2_2_metric_population.py`, SHA-256 `30cbd4d5a99e655f300a7c7a4a595dc46a6a642fe17fa35d42b5fdf8a2840f46`.

## Achados bloqueantes

### R1 — P1: colisão de identidade mistura o log e a decisão básica

Local: `metric_population.py:240–248`. `_stage_a` conserva o primeiro log por ID (`setdefault`), mas a última decisão de elegibilidade por ID. M2.1 §4 explicitamente preserva entradas repetidas; a interface M2.2 não estabelece uma pré-condição de unicidade.

Contraexemplo executado: alvo Mage/Fire; duas referências `REPORT:1:Duplicate`, uma Warrior/Fire e outra Mage/Fire, demais campos iguais. M2.1 retorna respectivamente `INELIGIBLE` e `ELIGIBLE`. Na ordem incompatível→compatível, M2.2 admite o log incompatível usando a decisão do outro objeto. Na ordem inversa, a população fica vazia. Outro caso, sem divergência de classe: duas representações do mesmo ID, ambas elegíveis, com uptime ausente/0.5; inverter a ordem muda N de `aura_uptime_fraction:1` de 0 para 1.

Viola AC3, §8.1 e a invariância de permutação de §11. Comportamento afetado: seleção local pela interface efetivamente aceita, antes de qualquer integração M2.3. Não é necessário implementar deduplicação de produto para demonstrar o defeito; o revisor não escolhe qual representação deve prevalecer. A entrega deve impedir a associação cruzada e provar o comportamento de colisões conforme contrato, encaminhando ambiguidade metodológica ao Opus se necessário.

### R2 — P2: exclusões não são ordenadas por reference_id

Local: `metric_population.py:268–281`, e construção de `common` no aspiracional. Com duas referências distintas excluídas, `REPORT:1:Z` por buff externo e `REPORT:1:A` por duração, a ordem das chaves é Z,A ou A,Z conforme a entrada. `json.dumps(dataclasses.asdict(result))` difere entre as permutações.

Viola §11: “Toda saída ordenada por reference_id”. O teste existente compara dicionários por igualdade, que ignora a ordem, e o teste de serialização usa `sort_keys=True` sobre duas avaliações da mesma ordem. Ambos passam sem verificar esse contrato. Resultado afetado: representação reproduzível de exclusões para as duas populações. Corrigir e demonstrar ordenação na própria saída, sem depender de normalização externa que esconda o defeito.

### R3 — P2: pacote incompleto para AC6

SPEC §12.9 exige executar sensibilidade sobre fixtures e metadados reais, preservando a tabela por nível (N e membros) nas evidências. A entrega contém dois testes sintéticos de sensibilidade e referências textuais a eles, mas nenhuma tabela preservada, nem chamada de `covariate_sensitivity` no replay real. §12.10 exige censo por métrica; o replay e a tabela apresentados cobrem somente `gross_ability_dps:1`, não as seis métricas. §12.1 exige variação isolada de cada covariável para cada métrica; a parametrização completa existente varia somente item level, enquanto os demais eixos não cobrem todas as métricas.

A ausência de `data/raw` no workspace está declarada e não é, por si só, bloqueante. Os metadados reais já disponíveis permitem produzir os artefatos exigidos sem coleta nova. Completar os testes e evidências contratados, sem ampliar escopo. A afirmação de que a matriz foi escrita antes da implementação consta da entrega, mas não há artefato temporal independente que permita ao revisor confirmar a cronologia.

## Matriz de aceite revisada

| Critério | Resultado independente |
|---|---|
| AC1 | Fixtures das seis métricas passam; sondagem adicional de 24 combinações isoladas confirma os membros esperados para IDs distintos. |
| AC2 | Casos locais passam; ordem tier→ilvl→duração, deltas zero e parada no piso conferidos no código. |
| AC3 | **Falha: R1**. A decisão básica pode ser associada a outro log do mesmo ID. |
| AC4 | Subconjunto, separação de N, guarda de DPS finito e limitações conferidos; testes locais passam. Ordenação das exclusões afetada por R2. |
| AC5 | Casos locais de disponibilidade por métrica/spell passam; R1 torna N dependente da ordem em colisões. |
| AC6 | **Falha: R3**. Fronteira local respeitada; evidências obrigatórias incompletas. |

Os achados são vinculados ao contrato da unidade, conforme a regra de closure do workflow. Não se exige integração, nova aquisição, calibração, robustez universal ou alteração de políticas M1/M2.1.

## Reprodução e validação

Ambiente independente: Windows 11 build 26200, Python 3.14.6, `.venv` local do produto. Comandos executados a partir desse workspace:

```powershell
.venv/Scripts/python.exe -B docs/submilestones/M2.2/independent-probes.py.txt
.venv/Scripts/python.exe -B -m pytest -o addopts='' -p no:cacheprovider -m 'not network' -q tests/unit/test_m2_2_metric_population.py tests/unit/test_m2_1_reference_eligibility.py
.venv/Scripts/python.exe -m ruff check .
.venv/Scripts/python.exe -m ruff format --check .
.venv/Scripts/python.exe -m pyright
```

Sondagens R1/R2 reproduzidas com asserts: decisões `[INELIGIBLE, ELIGIBLE]` admitem `REPORT:1:Duplicate`; ordem inversa exclui; N de uptime muda `[0, 1]`; exclusões mudam `[Z,A]` para `[A,Z]`. Script preservado ao lado deste relatório.

Seleção M2.1+M2.2: **87 passed, 2 skipped** (corpus opcional ausente). Ruff lint passou; formato: 331 arquivos já formatados; Pyright: 0 erros/avisos. Sondagem adicional dos limiares N=0,7,8,14,15,16 confirmou os estados contratados.

A suíte ampla foi executada com os cinco placeholders públicos de CI (`DISCORD_TOKEN`, `WCL_CLIENT_ID`, `WCL_CLIENT_SECRET`, `BLIZZARD_CLIENT_ID`, `BLIZZARD_CLIENT_SECRET` = `ci-placeholder-not-a-secret`) e comando:

```powershell
.venv/Scripts/python.exe -B -m pytest -o addopts='' -p no:cacheprovider -m 'not network' -q
```

Resultado amplo independente: **1 failed, 2658 passed, 47 skipped, 1 deselected, 337 warnings**, 241,70 s; 2 snapshots passaram. Única falha: `tests/unit/test_logging_setup.py::test_no_raw_print_calls_anywhere_in_src`, com offender `src/botgitgud/orchestrator/__main__.py`. Reproduz exatamente a falha preexistente documentada no fechamento de M2.1; não é usada como bloqueio novo de M2.2. Os totais coincidem com os apresentados pelo executor. Warnings de depreciação em pytest/Discord; skips de corpus opcional/plataforma presentes na suíte. A execução ampla não revela regressão adicional, mas não cobre os contraexemplos R1/R2.

Limites: as sondagens reutilizam apenas construtores de dados dos testes entregues; os contraexemplos e expectativas são independentes. Nenhum teste existente, implementação ou SPEC foi alterado. Somente este relatório e seu script de reprodução foram acrescentados pela revisão.
