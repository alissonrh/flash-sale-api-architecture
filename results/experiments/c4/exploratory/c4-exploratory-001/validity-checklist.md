# Checklist de validade

**Validade: INVALID**
**Estabilidade: NOT_EVALUATED**

- [x] Preflight do C4 aprovado
- [ ] Node permaneceu Ready durante a coleta
- [ ] Coletor terminou sem falhas
- [x] Logs, serie temporal e resumos do k6 foram copiados antes da remocao do Job
- [ ] Banco, Prometheus, logs, traces e Kubernetes foram exportados
- [ ] Todos os arquivos obrigatorios estao presentes
- [x] k6 terminou com codigo 0 (observado: 0)

## Estabilidade e saturacao

- Drenagem concluida: sim.
- Objetivo de drenagem de 180 segundos: atingido.
- Reinicializacoes durante a execucao (delta): 0.
- Reinicializacoes de API/worker (delta): 0.
- Desempenho ruim da aplicacao, isoladamente, nao invalida a execucao.

## Protecao de entrada do C4

- [ ] Protecao funcionando: respostas 429 controladas observadas.
- [ ] Invariante de classificacao das requisicoes satisfeita.
- [ ] Banco reconciliado: somente respostas 2xx criaram pedidos e todos foram concluidos.
- Requisicoes iniciadas: 0.
- Aceitas (2xx): 0.
- Protegidas/rejeitadas (429): 0 (0.000%).
- Falhas inesperadas: 0 (5xx=0, conexao=0, outros=0).
- Reinicializacoes do gateway (delta): 0.
- Respostas 429 sao o tratamento experimental esperado e nao causam, por si so, invalidade ou instabilidade.

## Autoscaling da API no C4

- Replicas da API observadas (min/max): /.
- Maximo desejado pelo HPA: .
- Primeira decisao do HPA acima de 1: nao observada.
- Primeira observacao de multiplos Pods: nao observada.
- Criacao e remocao de Pods pelo HPA nao sao contabilizadas como reinicializacoes.

## Motivos de invalidade

- coletor terminou com codigo 1
- Exportacao de evidencias: linha 390: wait "$COLLECTOR_PID"
