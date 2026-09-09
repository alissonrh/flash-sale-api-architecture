# Checklist de validade

**Validade: INVALID**
**Estabilidade: NOT_EVALUATED**

- [x] Preflight do C1 aprovado
- [ ] Node permaneceu Ready durante a coleta
- [x] Coletor terminou sem falhas
- [x] Logs, serie temporal e resumos do k6 foram copiados antes da remocao do Job
- [ ] Banco, Prometheus, logs, traces e Kubernetes foram exportados
- [ ] Todos os arquivos obrigatorios estao presentes
- [x] k6 terminou com codigo 0 (observado: 0)

## Estabilidade e saturacao

- Drenagem concluida: nao.
- Objetivo de drenagem de 180 segundos: nao atingido.
- Reinicializacoes durante a execucao (delta): 0.
- Reinicializacoes de API/worker (delta): 0.
- Desempenho ruim da aplicacao, isoladamente, nao invalida a execucao.

## Motivos de invalidade

- Exportacao de evidencias: nao foi possivel preservar todos os logs de containers anteriores
