# Revisão independente preservada e regressões

Os cinco artefatos originais foram copiados, sem alteração de bytes, de
`C:/Users/wsgon/AppData/Local/Temp/m1-independent-review-mp3r5oc5/`.
[archive-sha256.json](archive-sha256.json) identifica cada cópia.
O script original foi arquivado como `.py.txt`: suas asserções demonstram os
defeitos da árvore revisada, não o comportamento esperado da implementação corrigida.

A evidência anterior de implementação foi invalidada pela revisão e está em
[superseded-implementation-evidence.md.txt](superseded-implementation-evidence.md.txt).
Ela não comprova a árvore atual. A evidência vigente fica em
[m1-review-evidence.md](../../m1-review-evidence.md).

Antes de alterar `src`, foram introduzidas as regressões de
[test_m1_required_changes.py](../../../tests/unit/test_m1_required_changes.py).
As 25 instâncias iniciais falharam por asserção do contrato violado: não houve
erro de importação, montagem de fixture ou coleta que substituísse o contraexemplo.
Os registros dessa execução são [JUnit](regressions-before.xml),
[saída completa](regressions-before.log.txt) e [exit 1](regressions-before-exit.txt).
Os testes foram depois ampliados com controles positivos e integração.

```powershell
.venv/Scripts/python.exe -m pytest -o addopts='' -m 'not network' -q --tb=short tests/unit/test_m1_required_changes.py
```

U é a numeração do pedido de correção; R é a numeração da revisão preservada.
R12 consta das sondagens e de seu JSON, além da matriz da revisão.

| Grupo | Revisão | Instâncias vermelhas antes da correção | Contrato permanente |
|---|---|---:|---|
| U01 | R1 | 4 | Cast negativo, fora da luta, NaN ou infinito não autoriza coaching; CLI/Discord não o reintroduzem |
| U02 | R12 | 1 | Uptime de pet não autoriza coaching; entidade elegível mantém a comparação independente de uptime |
| U03 | R2 | 4 | Cursor ausente e evento fora do intervalo bloqueiam COMPLETE; página vazia terminal explícita continua válida; fetch integrado preserva o estado |
| U04 | R3 | 5 | Intervalo ausente, invertido ou insuficiente e reconciliação contraditória bloqueiam accounting após Parquet |
| U05 | R4 | 1 | Autoridade WCL continua exata, inclusive após round-trip |
| U06 | R5 | 2 | Uptime ausente/null não vira zero; zero explícito permanece medido |
| U07 | R6 | 2 | Features/core consomem a validação de cobertura e valor; dano permanece independente |
| U08 | R7 | 2 | Cabeçalho, conclusão e uptime transportam suas populações/fontes; piso público e suficiência de grade são respeitados |
| U09 | R8 | 1 | D=0/hits>0 mantém eventos/s e dano/evento disponíveis |
| U10 | R9 | 1 | Overflow de soma, DPS, percentual e estatística produz abstenção explícita |
| U11 | R10 | 1 | Pesos 1/N, unclassified apenas nos pares sem split e tolerância normativa; mutações erradas são rejeitadas |
| U12 | R11 | 1 | Contrato ativo sem campos legados de dano, produto de médias, share antigo ou ganho estimado |

U11 também é verificado por A07 com componentes numéricos independentes e pelo
oráculo com `fractions.Fraction` em
[test_m1_algebra_properties.py](../../../tests/unit/test_m1_algebra_properties.py).
O oráculo não chama accounting, split ou tolerância da implementação para calcular
seus valores esperados. Ele verifica componentes, suporte e linhas omitidas, com
durações distintas, zeros, IDs ausentes e pares compatíveis/incompatíveis.
