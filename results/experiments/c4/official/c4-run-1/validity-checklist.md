# Checklist de validade

**Validade: VALID**
**Estabilidade: UNSTABLE**

- [x] Preflight do C4 aprovado
- [x] Node permaneceu Ready durante a coleta
- [x] Coletor terminou sem falhas
- [x] Logs, serie temporal e resumos do k6 foram copiados antes da remocao do Job
- [x] Banco, Prometheus, logs, traces e Kubernetes foram exportados
- [x] Todos os arquivos obrigatorios estao presentes
- [x] k6 terminou com codigo 0 (observado: 0)

## Estabilidade e saturacao

- Drenagem concluida: sim.
- Objetivo de drenagem de 180 segundos: atingido.
- Reinicializacoes durante a execucao (delta): 1.
- Reinicializacoes de API/worker (delta): 0.
- Desempenho ruim da aplicacao, isoladamente, nao invalida a execucao.

## Protecao de entrada do C4

- [x] Protecao funcionando: respostas 429 controladas observadas.
- [x] Invariante de classificacao das requisicoes satisfeita.
- [x] Banco reconciliado: somente respostas 2xx criaram pedidos e todos foram concluidos.
- Requisicoes iniciadas: 1609.
- Aceitas (2xx): 1519.
- Protegidas/rejeitadas (429): 90 (5.594%).
- Falhas inesperadas: 0 (5xx=0, conexao=0, outros=0).
- Reinicializacoes do gateway (delta): 1.
- Respostas 429 sao o tratamento experimental esperado e nao causam, por si so, invalidade ou instabilidade.

## Autoscaling da API no C4

- Replicas da API observadas (min/max): 1/5.
- Maximo desejado pelo HPA: 5.
- Primeira decisao do HPA acima de 1: 2026-09-21T23:44:09.346Z.
- Primeira observacao de multiplos Pods: 2026-09-21T23:44:09.346Z.
- Criacao e remocao de Pods pelo HPA nao sao contabilizadas como reinicializacoes.

### Sinais observados

- 1 reinicializacao(oes) de container durante a execucao
- 1 reinicializacao(oes) no gateway sob carga
