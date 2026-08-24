# Gate humano de rotação de credenciais (R0-01)

- Revogar e reemitir o token do bot no Discord Developer Portal.
- Reemitir WCL client id/secret e confirmar que o par antigo falha.
- Reemitir Blizzard client id/secret e confirmar que o par antigo falha.
- Atualizar apenas `.env` local; nunca colar valores em issue, chat, teste ou documento.
- Rodar o smoke R1-01 com as novas credenciais e registrar somente evidência sanitizada.
- Confirmar `git status` e repetir a auditoria R0-02 antes de qualquer publicação.

Variáveis afetadas: `DISCORD_TOKEN`, `WCL_CLIENT_ID`, `WCL_CLIENT_SECRET`,
`BLIZZARD_CLIENT_ID`, `BLIZZARD_CLIENT_SECRET`. Este checklist não constitui evidência de rotação;
R0-01 permanece `BLOCKED_ON_HUMAN`.
