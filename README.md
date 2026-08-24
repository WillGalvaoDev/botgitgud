# botgitgud

Bot Discord para analisar cooldowns e métricas de performance de um jogador de World of Warcraft
contra uma coorte pareada de logs comparáveis do Warcraft Logs (WCL). A v1.0 usa somente o caminho
estatístico das Fases 0–3: não há modelo preditivo ou ML exposto ao jogador, e
`phase4_model_registry` permanece vazio.

## Requisitos e instalação

Python 3.11 ou superior, uma aplicação Discord, credenciais de desenvolvedor WCL e Blizzard.
Neste ambiente `uv` não está disponível; use `venv` e pip:

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -e ".[data,dev]"
```

## Configuração

Copie `.env.example` para `.env` e preencha somente no arquivo local as cinco credenciais:
`DISCORD_TOKEN`, `WCL_CLIENT_ID`, `WCL_CLIENT_SECRET`, `BLIZZARD_CLIENT_ID` e
`BLIZZARD_CLIENT_SECRET`. O template documenta também todos os defaults de rede, orçamento,
coorte e operação. Nunca versione `.env`. Alterar parâmetros metodológicos muda as comparações;
o `settings_hash` registra essa deriva.

## CLI

- `analyze`: análise síncrona para diagnóstico/CLI.
- `build-cohort`: aquece sob demanda uma coorte.
- `serve`: inicia Discord e worker persistente.
- `probe-schema`: sonda o schema WCL ao vivo e consome API.
- `discover`, `triage`, `dataset-status`: ferramentas offline/de aquisição já existente.
- `ops-status`: saúde local read-only, fila e warehouse; não chama API.
- `recover-jobs`: devolve jobs `running` para `queued` após crash.
- `experiment-plan`, `experiment-status`, `experiment-collect`, `experiment-evaluate`,
  `experiment-decide`, `experiment-calibrate`: trilha experimental histórica, fora da v1.0; não
  execute coleta/campanha sem um roadmap e autorização próprios.

O antigo `backfill` era um stub sem requisito e foi removido. Veja opções e códigos de saída com
`python -m botgitgud.cli <comando> --help`. Para iniciar o serviço:

```powershell
.venv\Scripts\python.exe -m botgitgud.cli serve
```

## Arquitetura

`wcl/` e `blizzard/` isolam APIs; `ingest/` persiste DuckDB/Parquet; `analysis/` faz matching,
alinhamento, grading e decomposição do gap; `report/` gera resumo e HTML; `bot/` mantém fila,
worker e handlers Discord. Uma análise quente entrega resumo + `relatorio.html`; uma coorte fria é
enfileirada e entrega o mesmo contrato quando termina.

## Testes

```powershell
.venv\Scripts\python.exe -m pytest
.venv\Scripts\ruff.exe check .
.venv\Scripts\ruff.exe format --check .
.venv\Scripts\pyright.exe src tests
```

## Limitações conhecidas

- O Top 3 ranqueia apenas achados cujo ganho de DPS é quantificável; outros problemas continuam
  nas seções detalhadas.
- Diferenças de talento podem aparecer como `(nodeID, rank)`, pois as APIs verificadas não expõem
  nomes confiáveis para esses nós.
- A banda posicional conservadora pode reduzir a amostra; o relatório sinaliza baixa confiança ou
  amostra insuficiente.
- Sem fonte verificável de cooldown base, MAJOR/MINOR usa intervalo/contagem observados. Isso pode
  mudar o rótulo da seção, não a evidência comparativa do achado.
- O golden de gap de DPS usa referências truncadas e protege estrutura/regressão, não números
  realistas de coorte. A matemática é coberta por testes sintéticos e property-based.

Operação, incidentes e backup estão em `docs/runbook.md`.
