# Checklist de validade

**Validade: VALID**
**Estabilidade: STABLE**

- [x] Preflight do C1 aprovado
- [x] Node permaneceu Ready durante a coleta
- [x] Coletor terminou sem falhas
- [x] Logs, serie temporal e resumos do k6 foram copiados antes da remocao do Job
- [x] Banco, Prometheus, logs, traces e Kubernetes foram exportados
- [x] Todos os arquivos obrigatorios estao presentes
- [x] k6 terminou com codigo 0 (observado: 0)

## Estabilidade e saturacao

- Drenagem concluida: sim.
- Objetivo de drenagem de 180 segundos: atingido.
- Reinicializacoes durante a execucao (delta): 0.
- Reinicializacoes de API/worker (delta): 0.
- Desempenho ruim da aplicacao, isoladamente, nao invalida a execucao.
