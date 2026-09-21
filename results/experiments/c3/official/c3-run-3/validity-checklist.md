# Checklist de validade

**Validade: INVALID**
**Estabilidade: UNSTABLE**

- [x] Preflight do C3 aprovado
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
- Reinicializacoes de API/worker (delta): 1.
- Desempenho ruim da aplicacao, isoladamente, nao invalida a execucao.

## Protecao de entrada do C3

- [x] Protecao funcionando: respostas 429 controladas observadas.
- [ ] Invariante de classificacao das requisicoes satisfeita.
- [ ] Banco reconciliado: somente respostas 2xx criaram pedidos e todos foram concluidos.
- Requisicoes iniciadas: 1563.
- Aceitas (2xx): 1349.
- Protegidas/rejeitadas (429): 91 (5.822%).
- Falhas inesperadas: 19 (5xx=19, conexao=0, outros=0).
- Reinicializacoes do gateway (delta): 0.
- Respostas 429 sao o tratamento experimental esperado e nao causam, por si so, invalidade ou instabilidade.

### Sinais observados

- 1 reinicializacao(oes) de container durante a execucao
- 1 reinicializacao(oes) em API/worker sob carga
- 19 falha(s) inesperada(s): 5xx=19, conexao=0, outros=0

## Motivos de invalidade

- classificacao do C3 nao satisfaz a igualdade de requisicoes iniciadas
- banco nao reconcilia respostas 2xx com pedidos criados e concluidos
