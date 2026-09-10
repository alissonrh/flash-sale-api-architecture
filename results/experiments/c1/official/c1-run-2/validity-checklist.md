# Checklist de validade

**Validade: VALID**
**Estabilidade: UNSTABLE**

- [x] Preflight do C1 aprovado
- [x] Node permaneceu Ready durante a coleta
- [x] Coletor terminou sem falhas
- [x] Logs, serie temporal e resumos do k6 foram copiados antes da remocao do Job
- [x] Banco, Prometheus, logs, traces e Kubernetes foram exportados
- [x] Todos os arquivos obrigatorios estao presentes
- [x] k6 terminou com codigo 0 (observado: 0)

## Estabilidade e saturacao

- Drenagem concluida: nao.
- Objetivo de drenagem de 180 segundos: nao atingido.
- Reinicializacoes durante a execucao (delta): 1.
- Reinicializacoes de API/worker (delta): 1.
- Desempenho ruim da aplicacao, isoladamente, nao invalida a execucao.

### Sinais observados

- 1 reinicializacao(oes) de container durante a execucao
- 1 reinicializacao(oes) em API/worker sob carga
- drenagem nao concluiu em 600 segundos
